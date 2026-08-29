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
        uids = client.datasource_uids()
    except GrafanaError as exc:
        print(f"{BAD}{exc}")
        return report(["token likely lacks permission to list datasources"])

    loki = next((u for t, u in uids.items() if t == "loki"), None)
    tempo = next((u for t, u in uids.items() if t == "tempo"), None)
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
                print(f"{OK}Authorization present — instance id {instance}")
            except Exception:
                print(
                    f"{WARN}Authorization present but not decodable base64 — check the value"
                )
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
