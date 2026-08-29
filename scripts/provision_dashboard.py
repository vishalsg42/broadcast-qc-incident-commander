#!/usr/bin/env python
"""Install the QC dashboard into Grafana, and verify its panels return data.

    python scripts/provision_dashboard.py

Provisioned rather than screenshotted. A dashboard JSON in a repo that nobody
has imported is a claim; one that installs and then answers its own queries is
evidence. This script does both, and reports per-panel whether real data came
back so a broken query cannot pass for an empty time range.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.error
import urllib.request

from dotenv import load_dotenv

from agent.grafana import GrafanaClient, GrafanaConfig

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "grafana" / "dashboard.json"


def install(config: GrafanaConfig, dashboard: dict) -> str:
    body = json.dumps(
        {"dashboard": dashboard, "overwrite": True, "message": "provisioned by script"}
    ).encode()
    req = urllib.request.Request(
        f"{config.url.rstrip('/')}/api/dashboards/db",
        data=body,
        headers={
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    return f"{config.url.rstrip('/')}{result['url']}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    config = GrafanaConfig.from_env()
    dashboard = json.loads(DASHBOARD.read_text())

    # The uids in the file are this stack's. Rewrite them from the environment
    # so the dashboard is not silently pinned to one Grafana account.
    text = json.dumps(dashboard)
    text = text.replace("grafanacloud-logs", config.loki_uid)
    text = text.replace("grafanacloud-traces", config.tempo_uid)
    dashboard = json.loads(text)

    print(f"installing into {config.url}")
    try:
        url = install(config, dashboard)
    except urllib.error.HTTPError as exc:
        print(f"FAILED {exc.code}: {exc.read().decode()[:300]}")
        return 1
    print(f"  installed -> {url}\n")

    # Verify the queries, not just the import. An empty panel and a broken query
    # look identical in a screenshot.
    client = GrafanaClient(config)
    checks = [
        (
            "QC measurements",
            lambda: len(
                client.query_logs(
                    '{service_name="qc-pipeline"} | qc_stage != ``',
                    lookback_s=21600,
                    limit=20,
                )
            ),
        ),
        (
            "Delivery traces",
            lambda: len(
                client.search_traces('{name="delivery.run"}', lookback_s=21600, limit=20)
            ),
        ),
        (
            "Stage spans (attribution)",
            lambda: len(
                client.search_traces('{name =~ "stage.*"}', lookback_s=21600, limit=20)
            ),
        ),
    ]
    ok = True
    for name, run in checks:
        try:
            count = run()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            print(f"  {name:<28} ERROR {exc}")
            ok = False
            continue
        mark = "ok" if count else "NO DATA"
        if not count:
            ok = False
        print(f"  {name:<28} {mark} ({count} results in the last 6h)")

    print(
        "\nEvery panel's query returned data."
        if ok
        else "\nSome panels returned nothing - "
        "run a delivery first, or widen the dashboard time range."
    )
    # Worth stating plainly: this proves the QUERIES work, not that every panel
    # can render what they return. A `traces` visualisation given a search rather
    # than a trace id drew "No data found in response" while this same check
    # reported twenty results, which is how that panel shipped broken.
    print("This checks the queries, not the rendering. Open the dashboard too.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
