"""Grafana write-back: dashboard annotations and IRM incidents.

Deliberately a SEPARATE module with a SEPARATE credential from `grafana.py`.

The investigation runs with a read-only token. Only after a conclusion has passed
the citation validator, and a human has approved the repair, does anything reach
this module - which uses a narrowly-scoped write token. Restricting which tools a
model can see is not a security boundary; the credential is. If the read path is
compromised it still cannot write.

Annotations work against any Grafana. Incidents are a Grafana Cloud IRM feature
and are absent from OSS/local, so incident creation degrades to a no-op with a
clear reason rather than failing the run.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import urllib3

from .grafana import GrafanaError


@dataclass(frozen=True)
class WriterConfig:
    url: str
    token: str | None = None

    @classmethod
    def from_env(cls) -> WriterConfig:
        return cls(
            url=os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/"),
            # Falls back to the read token so local development works unchanged;
            # in Cloud these MUST be two different service accounts.
            token=(
                os.environ.get("GRAFANA_WRITE_TOKEN")
                or os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN")
                or None
            ),
        )


@dataclass(frozen=True)
class WriteResult:
    kind: str
    ok: bool
    detail: str
    remote_id: str | None = None


class GrafanaWriter:
    """Write-side Grafana access. Never used during investigation."""

    def __init__(self, config: WriterConfig | None = None, *, timeout: float = 15.0):
        self.config = config or WriterConfig.from_env()
        self._timeout = timeout
        self._http = urllib3.PoolManager()

    def _post(self, path: str, body: dict) -> tuple[int, dict | str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        try:
            resp = self._http.request(
                "POST",
                f"{self.config.url}{path}",
                body=json.dumps(body).encode(),
                headers=headers,
                timeout=self._timeout,
            )
        except urllib3.exceptions.HTTPError as exc:
            raise GrafanaError(f"POST {path} failed: {exc}") from exc
        try:
            return resp.status, resp.json()
        except Exception:
            return resp.status, resp.data.decode(errors="replace")[:400]

    def annotate(
        self,
        *,
        text: str,
        tags: list[str],
        time_ms: int,
        time_end_ms: int | None = None,
    ) -> WriteResult:
        """Add a dashboard annotation marking what happened and when."""
        body: dict = {"text": text, "tags": tags, "time": time_ms}
        if time_end_ms:
            body["timeEnd"] = time_end_ms
        status, payload = self._post("/api/annotations", body)
        if status >= 400:
            return WriteResult("annotation", False, f"HTTP {status}: {payload}")
        remote_id = str(payload.get("id")) if isinstance(payload, dict) else None
        return WriteResult("annotation", True, "annotation created", remote_id)

    def create_incident(self, *, title: str, severity: str = "minor") -> WriteResult:
        """Open a Grafana IRM incident.

        Cloud-only. Local Grafana has no Incident app, so a 404 here means
        "not available in this environment", not "the request was wrong" - and
        the demo should carry on rather than fail.
        """
        status, payload = self._post(
            "/api/plugins/grafana-irm-app/resources/api/v1/IncidentsService.CreateIncident",
            {"title": title, "severity": severity},
        )
        if status == 404:
            return WriteResult(
                "incident",
                False,
                "Grafana IRM not available (Cloud-only feature); annotation still written",
            )
        if status >= 400:
            return WriteResult("incident", False, f"HTTP {status}: {payload}")
        incident_id = None
        if isinstance(payload, dict):
            incident = payload.get("incident") or {}
            incident_id = str(incident.get("incidentID") or payload.get("incidentID") or "")
        return WriteResult("incident", True, "incident created", incident_id or None)


def annotation_text(
    *,
    asset_id: str,
    failing_stage: str,
    preset_id: str,
    preset_version: int,
    measured: float,
    target: float,
    resolved: bool,
) -> str:
    """One line a human can read months later without opening the run."""
    outcome = "repaired and re-validated" if resolved else "BLOCKED, awaiting repair"
    return (
        f"{asset_id}: {measured} LUFS vs target {target} - attributed to "
        f"{failing_stage} preset {preset_id} v{preset_version}; {outcome}"
    )
