#!/usr/bin/env python
"""Verify Grafana credentials and discover Cloud's datasource UIDs.

    python scripts/check_grafana.py

Two separate credentials are needed and they fail in different ways:

  GRAFANA_SERVICE_ACCOUNT_TOKEN   queries Loki/Tempo and writes annotations,
                                  through Grafana's HTTP API
  OTEL_EXPORTER_OTLP_HEADERS      ships telemetry INTO Grafana Cloud, through
                                  the OTLP gateway

Creating only the first is the common mistake: queries work, but there is
nothing to query because ingestion was never authenticated.

Prints the real datasource UIDs so they can be pinned in .env - Grafana Cloud
generates stack-specific ones, so a local-to-Cloud move is a remap, not a URL
swap.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agent.annotations import GrafanaWriter, WriterConfig  # noqa: E402
from agent.grafana import GrafanaClient, GrafanaConfig, GrafanaError  # noqa: E402

OK = "  ok   "
BAD = "  FAIL "
WARN = "  warn "


def main() -> int:
    url = os.environ.get("GRAFANA_URL", "")
    is_cloud = "grafana.net" in url
    problems: list[str] = []

    print(f"target: {url or '<unset>'}  ({'Grafana Cloud' if is_cloud else 'local'})\n")

    # ---- 1. query credential -------------------------------------------------
    print("1. Querying (service account token)")
    token = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN")
    if is_cloud and not token:
        print(f"{BAD}GRAFANA_SERVICE_ACCOUNT_TOKEN is unset")
        problems.append(
            "create a STACK service account token (Administration > Users and access)"
        )
    client = GrafanaClient(GrafanaConfig.from_env())
    try:
        health = client.health()
        print(f"{OK}reachable — Grafana {health.get('version')}")
    except GrafanaError as exc:
        print(f"{BAD}{exc}")
        problems.append("check GRAFANA_URL and the service account token")
        return report(problems)

    # ---- 2. datasource UIDs --------------------------------------------------
    print("\n2. Datasource UIDs")
    try:
        client.datasource_uids()
    except GrafanaError as exc:
        print(f"{BAD}{exc}")
        return report(["token likely lacks permission to list datasources"])

    # A Cloud stack ships SEVERAL datasources of the same type - a Loki for
    # application logs, another for alert state history, another for usage
    # insights. Taking the first match silently queries the wrong one and
    # returns nothing, which reads as a broken integration.
    all_ds = client._get("/api/datasources")
    loki = _pick(all_ds, "loki", prefer=("-logs",), avoid=("usage", "alert-state"))
    tempo = _pick(all_ds, "tempo", prefer=("-traces",), avoid=())

    for kind in ("loki", "tempo"):
        same = [d for d in all_ds if d["type"] == kind]
        if len(same) > 1:
            chosen = loki if kind == "loki" else tempo
            print(f"{WARN}{len(same)} {kind} datasources on this stack; chose {chosen}")
            for d in same:
                mark = "->" if d["uid"] == chosen else "  "
                print(f"       {mark} {d['uid']}")
    for kind, uid in (("loki", loki), ("tempo", tempo)):
        print(f"{OK if uid else BAD}{kind:6s} {uid or '<not found>'}")
    if not (loki and tempo):
        problems.append("this stack has no Loki and/or Tempo datasource")

    configured = (os.environ.get("GRAFANA_LOKI_UID"), os.environ.get("GRAFANA_TEMPO_UID"))
    if (loki, tempo) != configured and loki and tempo:
        print("\n  Put these in .env — Cloud UIDs are stack-specific:")
        print(f"    GRAFANA_LOKI_UID={loki}")
        print(f"    GRAFANA_TEMPO_UID={tempo}")

    # ---- 3. ingestion credential --------------------------------------------
    print("\n3. Shipping telemetry in (OTLP)")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    print(f"{OK if endpoint else BAD}endpoint {endpoint or '<unset>'}")
    if is_cloud:
        if not headers:
            print(f"{BAD}OTEL_EXPORTER_OTLP_HEADERS is unset")
            problems.append(
                "Cloud OTLP needs auth: Connections > Add new connection > OpenTelemetry"
            )
        elif "authorization" not in headers.lower():
            print(f"{BAD}headers set but contain no Authorization")
            problems.append(
                "OTEL_EXPORTER_OTLP_HEADERS must carry Authorization=Basic <base64>"
            )
        else:
            value = headers.split("Basic", 1)[-1].strip() if "Basic" in headers else ""
            try:
                decoded = base64.b64decode(value).decode()
                instance = decoded.split(":", 1)[0]
                print(f"{OK}Authorization decodes — instance id {instance}")
            except Exception:
                print(f"{BAD}Authorization is not decodable base64")
                problems.append(
                    "OTEL_EXPORTER_OTLP_HEADERS looks malformed. The value contains a "
                    "SPACE, so it must be QUOTED in .env or the shell truncates it at "
                    "'Basic' and the credential is silently dropped."
                )
                instance = None

            # Checking the string is not enough - it looked correct while the
            # shell was truncating it, and the gateway answered 'no credentials
            # provided'. Actually post, so a false green is impossible.
            if instance:
                status, body = _probe_otlp(endpoint, headers)
                if status == 200:
                    print(f"{OK}gateway accepted a test payload (HTTP 200)")
                elif status == 401:
                    print(f"{BAD}gateway rejected the credential — HTTP 401: {body}")
                    problems.append(
                        "OTLP auth failed. If the body says 'no credentials provided', "
                        "the header is being truncated: QUOTE the value in .env."
                    )
                else:
                    print(f"{WARN}gateway returned HTTP {status}: {body}")
        if endpoint and "otlp" not in endpoint:
            print(f"{WARN}endpoint has no /otlp path; Cloud usually ends in /otlp")
    else:
        print(f"{OK}local stack needs no OTLP auth")

    # ---- 4. write path -------------------------------------------------------
    print("\n4. Writing (annotations, IRM)")
    writer = GrafanaWriter(WriterConfig.from_env())
    import time

    result = writer.annotate(
        text="qcic credential check", tags=["qcic-check"], time_ms=int(time.time() * 1000)
    )
    print(f"{OK if result.ok else BAD}annotation — {result.detail}")
    if not result.ok:
        problems.append("annotation write failed; the token needs Editor")

    incident = writer.create_incident(title="qcic credential check")
    if incident.ok:
        print(f"{OK}IRM incident — {incident.detail}")
    elif is_cloud:
        print(f"{BAD}IRM — {incident.detail}")
        problems.append("IRM unavailable on this Cloud stack; check the IRM app is enabled")
    else:
        print(f"{WARN}IRM — {incident.detail}")

    return report(problems)


def _probe_otlp(endpoint: str, headers: str) -> tuple[int, str]:
    """POST an empty payload to the OTLP gateway to prove the credential works."""
    import urllib3

    auth = headers.split("=", 1)[1] if headers.startswith("Authorization=") else headers
    try:
        resp = urllib3.PoolManager().request(
            "POST",
            f"{endpoint.rstrip('/')}/v1/traces",
            body=b'{"resourceSpans":[]}',
            headers={"Content-Type": "application/json", "Authorization": auth},
            timeout=15,
        )
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    return resp.status, resp.data.decode(errors="replace")[:160]


def _pick(datasources: list[dict], kind: str, *, prefer: tuple, avoid: tuple) -> str | None:
    """Choose the datasource actually carrying application telemetry."""
    candidates = [d for d in datasources if d["type"] == kind]
    if not candidates:
        return None
    wanted = [
        d
        for d in candidates
        if any(p in d["uid"] for p in prefer) and not any(a in d["uid"] for a in avoid)
    ]
    if wanted:
        return wanted[0]["uid"]
    plain = [d for d in candidates if not any(a in d["uid"] for a in avoid)]
    return (plain or candidates)[0]["uid"]


def report(problems: list[str]) -> int:
    print()
    if not problems:
        print("All checks passed.")
        return 0
    print("Fix these:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
