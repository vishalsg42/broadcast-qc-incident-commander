"""Grafana query client for Loki and Tempo.

Everything goes through Grafana's datasource proxy rather than hitting Loki and
Tempo directly, so local and Grafana Cloud use one code path and one credential.

Datasource UIDs are configuration, not constants. Local `otel-lgtm` provisions
`loki` / `tempo`; Grafana Cloud generates stack-specific UIDs such as
`grafanacloud-<stack>-logs`. A cutover therefore needs a UID remap and is NOT an
environment-variable swap - see docs/DECISIONS.md.

This client is read-only by design. Writes (annotations, incidents) live
elsewhere and use a separate, narrower credential: tool filtering is not a
security boundary, credentials are.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass
from typing import Any

import urllib3

DEFAULT_TIMEOUT_S = 15.0


class GrafanaError(RuntimeError):
    """A Grafana query failed or returned something unusable."""


@dataclass(frozen=True)
class GrafanaConfig:
    url: str
    token: str | None = None
    loki_uid: str = "loki"
    tempo_uid: str = "tempo"

    @classmethod
    def from_env(cls) -> GrafanaConfig:
        url = os.environ.get("GRAFANA_URL", "http://localhost:3000").rstrip("/")
        return cls(
            url=url,
            token=os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") or None,
            loki_uid=os.environ.get("GRAFANA_LOKI_UID", "loki"),
            tempo_uid=os.environ.get("GRAFANA_TEMPO_UID", "tempo"),
        )


@dataclass(frozen=True)
class LogEntry:
    timestamp_ns: int
    line: str
    labels: dict[str, str]

    def label(self, key: str, default: str = "") -> str:
        return self.labels.get(key, default)


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_span_id: str | None
    name: str
    attributes: dict[str, Any]

    @property
    def is_root(self) -> bool:
        return not self.parent_span_id


class GrafanaClient:
    """Read-only access to Loki and Tempo through the Grafana datasource proxy."""

    def __init__(
        self, config: GrafanaConfig | None = None, *, timeout: float = DEFAULT_TIMEOUT_S
    ):
        self.config = config or GrafanaConfig.from_env()
        self._timeout = timeout
        self._http = urllib3.PoolManager()

    # -- transport ----------------------------------------------------------

    def _get(self, path: str, fields: dict[str, Any] | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        try:
            resp = self._http.request(
                "GET",
                f"{self.config.url}{path}",
                fields=fields or {},
                headers=headers,
                timeout=self._timeout,
            )
        except urllib3.exceptions.HTTPError as exc:
            raise GrafanaError(f"GET {path} failed: {exc}") from exc

        if resp.status == 401:
            raise GrafanaError(
                f"GET {path} -> 401. For Grafana Cloud this usually means a Cloud "
                "Access Policy token was used instead of a STACK service account token."
            )
        if resp.status >= 400:
            raise GrafanaError(f"GET {path} -> {resp.status}: {resp.data[:300]!r}")
        return resp.json()

    def _proxy(self, uid: str, sub_path: str) -> str:
        return f"/api/datasources/proxy/uid/{uid}{sub_path}"

    # -- health -------------------------------------------------------------

    def health(self) -> dict:
        return self._get("/api/health")

    def datasource_uids(self) -> dict[str, str]:
        """Map datasource type -> uid. Use this to discover Cloud's generated UIDs."""
        return {d["type"]: d["uid"] for d in self._get("/api/datasources")}

    # -- Loki ---------------------------------------------------------------

    def query_logs(
        self,
        logql: str,
        *,
        lookback_s: int = 3600,
        limit: int = 100,
        end_ns: int | None = None,
    ) -> list[LogEntry]:
        """Run a LogQL range query. Newest first."""
        end = end_ns if end_ns is not None else time.time_ns()
        start = end - lookback_s * 1_000_000_000
        data = self._get(
            self._proxy(self.config.loki_uid, "/loki/api/v1/query_range"),
            {"query": logql, "start": str(start), "end": str(end), "limit": str(limit)},
        )
        entries: list[LogEntry] = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                entries.append(LogEntry(int(ts), line, labels))
        entries.sort(key=lambda e: e.timestamp_ns, reverse=True)
        return entries

    # -- Tempo --------------------------------------------------------------

    def get_trace(self, trace_id: str) -> list[Span]:
        """Fetch every span in a trace, flattened."""
        data = self._get(self._proxy(self.config.tempo_uid, f"/api/traces/{trace_id}"))
        spans: list[Span] = []
        for batch in data.get("batches", []):
            for scope in batch.get("scopeSpans", []):
                for raw in scope.get("spans", []):
                    spans.append(
                        Span(
                            span_id=raw.get("spanId", ""),
                            parent_span_id=raw.get("parentSpanId") or None,
                            name=raw.get("name", ""),
                            attributes=_flatten_attributes(raw.get("attributes", [])),
                        )
                    )
        return spans

    def search_traces(
        self, traceql: str, *, lookback_s: int = 3600, limit: int = 20
    ) -> list[dict]:
        """TraceQL search. A time range is REQUIRED - without it Tempo returns
        an empty result rather than an error, which reads as 'no data'."""
        end = int(time.time())
        data = self._get(
            self._proxy(self.config.tempo_uid, "/api/search"),
            {
                "q": traceql,
                "start": str(end - lookback_s),
                "end": str(end),
                "limit": str(limit),
            },
        )
        return data.get("traces", [])

    def wait_for_traces(
        self,
        traceql: str,
        *,
        lookback_s: int = 3600,
        timeout_s: float = 120.0,
        interval_s: float = 3.0,
    ) -> list[dict]:
        """Poll until a TraceQL search returns something, or raise.

        Tempo ingestion lags span export by roughly 30s to 2min depending on
        flush configuration, and an un-ingested trace is indistinguishable from
        a missing one - both come back as an empty list.
        """
        deadline = time.time() + timeout_s
        while True:
            traces = self.search_traces(traceql, lookback_s=lookback_s)
            if traces:
                return traces
            if time.time() >= deadline:
                raise GrafanaError(
                    f"timed out after {timeout_s}s waiting for a trace matching "
                    f"{traceql!r}. Tempo ingestion can lag; if this persists the "
                    "query itself is probably wrong."
                )
            time.sleep(interval_s)

    def wait_for_logs(
        self,
        logql: str,
        *,
        expected: int = 1,
        timeout_s: float = 120.0,
        interval_s: float = 3.0,
    ) -> list[LogEntry]:
        """Poll until `expected` lines are queryable, or raise.

        Ingestion is eventually consistent and per-signal: a `pipeline.completed`
        marker does NOT prove the logs and traces behind it are both queryable.
        Poll for the specific evidence you need. 120s rather than 30s, because
        Tempo in particular can lag well past half a minute.
        """
        deadline = time.time() + timeout_s
        while True:
            entries = self.query_logs(logql, limit=max(expected, 20))
            if len(entries) >= expected:
                return entries
            if time.time() >= deadline:
                raise GrafanaError(
                    f"timed out after {timeout_s}s waiting for {expected} line(s) "
                    f"matching {logql!r}; got {len(entries)}"
                )
            time.sleep(interval_s)


def _flatten_attributes(attributes: list[dict]) -> dict[str, Any]:
    """OTLP attributes arrive as [{key, value:{stringValue|intValue|...}}]."""
    out: dict[str, Any] = {}
    for attr in attributes:
        value = attr.get("value", {})
        if not value:
            continue
        raw = next(iter(value.values()))
        if "intValue" in value:
            with contextlib.suppress(TypeError, ValueError):
                raw = int(raw)
        out[attr["key"]] = raw
    return out
