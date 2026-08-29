"""OpenTelemetry emission for the pipeline.

Signal routing, and why:

  Tempo (traces)  the asset's journey - one span per stage, carrying the preset
                  version that stage used. This is where ACTOR attribution comes
                  from, and it is the one view no other submission can produce.
  Loki (logs)     per-asset QC observations. QC results are TEST RECORDS, not
                  operational time-series; pushing per-measurement values into
                  Prometheus keyed by asset_id is a cardinality anti-pattern.

Two rules that make correlation actually work:

  1. Every log line carries `trace_id` and `span_id`. Without them the traces and
     the logs are two disconnected piles and there is no investigation to run.
  2. Label cardinality is deliberate. `stage`, `preset_id` and `preset_version`
     are low-cardinality and belong in resource attributes. `run_id`, `job_id`
     and `asset_id` are unbounded and stay in the log body.

Telemetry is optional: with no endpoint configured every helper becomes a no-op,
so tests and offline runs work unchanged.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME = "qc-pipeline"
LOGGER_NAME = "qcic.pipeline"

_state: _Telemetry | None = None

# How many callers currently hold telemetry open.
#
# init/shutdown are process-global, and the server runs deliveries concurrently.
# Without a count, the first run to finish tore down the providers the others
# were still writing through: their spans and log lines went nowhere, and their
# investigation then waited the full ingestion timeout for telemetry that had
# been dropped on the floor. Observed three times as a 300s "timed out waiting
# for 3 line(s)" before the cause was found.
_users = 0
_lock = threading.Lock()


@dataclass
class _Telemetry:
    tracer_provider: TracerProvider
    logger_provider: LoggerProvider
    endpoint: str
    handler: logging.Handler | None = None


def _endpoint() -> str | None:
    """OTLP HTTP base endpoint, e.g. http://localhost:4318."""
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None


def init(endpoint: str | None = None, *, service_name: str = SERVICE_NAME) -> bool:
    """Wire up trace and log export. Returns False if telemetry is disabled."""
    global _state, _users
    with _lock:
        if _state is not None:
            # Someone else already opened it. Join them rather than rebuilding.
            _users += 1
            return True

    endpoint = endpoint or _endpoint()
    if not endpoint:
        return False

    resource = Resource.create({"service.name": service_name, "service.version": "0.1.0"})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    # OTel permits set_tracer_provider ONCE per process, and warns on any repeat.
    # A long-lived server re-inits per run, so we only set the global the first
    # time; span creation goes through tracer() and this module's own provider,
    # which stays correct across re-inits either way.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(tracer_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
    )

    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    pipeline_logger = logging.getLogger(LOGGER_NAME)
    pipeline_logger.setLevel(logging.INFO)
    pipeline_logger.addHandler(handler)
    pipeline_logger.propagate = False

    with _lock:
        _state = _Telemetry(tracer_provider, logger_provider, endpoint, handler)
        _users += 1
    return True


def init_for_test(span_exporter) -> None:
    """Enable tracing against an in-memory exporter, for tests.

    Exists so the span hierarchy can be asserted without a running collector.
    Log export stays disabled; only trace structure is under test.
    """
    global _state, _users
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    resource = Resource.create({"service.name": f"{SERVICE_NAME}-test"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    _state = _Telemetry(tracer_provider, LoggerProvider(resource=resource), "memory://")
    _users = 1


def tracer() -> Any:
    """The tracer bound to this module's provider (not the global one)."""
    return (
        _state.tracer_provider.get_tracer(__name__) if _state else trace.get_tracer(__name__)
    )


def enabled() -> bool:
    return _state is not None


def shutdown() -> None:
    """Force-flush both providers.

    Batch processors export on a timer, so a short-lived process that exits
    without flushing silently drops the telemetry the investigation depends on.
    """
    global _state, _users
    with _lock:
        if _state is None:
            return
        _users -= 1
        if _users > 0:
            # Another delivery is still writing through these providers. Tearing
            # them down here is what silently dropped its telemetry.
            return
        _users = 0
        # Detach the handler before shutting its provider down. Without this a
        # server that re-inits per run accumulates handlers pointing at dead
        # providers, and log export degrades silently after the first run.
        if _state.handler is not None:
            logging.getLogger(LOGGER_NAME).removeHandler(_state.handler)
        state, _state = _state, None
    state.tracer_provider.shutdown()
    state.logger_provider.shutdown()


def current_trace_ids() -> tuple[str, str]:
    """(trace_id, span_id) as hex, or empty strings outside a span."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


@contextmanager
def run_span(*, run_id: str, asset_id: str, source_path: str) -> Iterator[Any]:
    """Root span for one delivery.

    Every stage span MUST be a child of this, otherwise each stage starts its own
    root trace and Tempo shows three unrelated single-span traces instead of one
    waterfall covering the asset's journey - which is the entire point of tracing
    an artefact rather than a service.
    """
    if not enabled():
        yield None
        return

    with tracer().start_as_current_span("delivery.run") as span:
        span.set_attribute("qc.run_id", run_id)
        span.set_attribute("qc.asset_id", asset_id)
        span.set_attribute("qc.source_path", source_path)
        yield span


@contextmanager
def stage_span(
    stage: str,
    *,
    preset_id: str,
    preset_version: int,
    preset_changed_at: str,
    run_id: str,
    asset_id: str,
    preset_changed_by: str | None = None,
    preset_change_ticket: str | None = None,
    preset_approved_by: str | None = None,
) -> Iterator[Any]:
    """One span per pipeline stage, carrying the preset that produced it.

    `preset_version` and `preset_changed_at` are the payoff of the entire demo -
    they are what turns "the audio is wrong" into "preset pkg_h264_v7, changed at
    14:02, remapped the channels". Easiest thing in the project to forget.

    The provenance attributes answer the question that always comes next: who
    changed it, under what ticket, and who approved it. They are unbounded
    values on the span, never promoted to metric labels.
    """
    if not enabled():
        yield None
        return

    with tracer().start_as_current_span(f"stage.{stage}") as span:
        span.set_attribute("qc.stage", stage)
        span.set_attribute("qc.preset_id", preset_id)
        span.set_attribute("qc.preset_version", preset_version)
        span.set_attribute("qc.preset_changed_at", preset_changed_at)
        # Provenance. Omitted rather than defaulted when absent, so a missing
        # attribute means "not recorded" instead of a fabricated value.
        for key, value in (
            ("qc.preset_changed_by", preset_changed_by),
            ("qc.preset_change_ticket", preset_change_ticket),
            ("qc.preset_approved_by", preset_approved_by),
        ):
            if value is not None:
                span.set_attribute(key, value)
        # Unbounded values: useful on the span, never promoted to a label.
        span.set_attribute("qc.run_id", run_id)
        span.set_attribute("qc.asset_id", asset_id)
        yield span


def emit_qc_observation(
    *,
    stage: str,
    run_id: str,
    asset_id: str,
    preset_id: str,
    preset_version: int,
    verdict: str,
    measurements: dict,
) -> None:
    """Emit one structured QC observation, correlated to the current span."""
    if not enabled():
        return
    trace_id, span_id = current_trace_ids()
    logging.getLogger(LOGGER_NAME).info(
        "qc observation stage=%s verdict=%s",
        stage,
        verdict,
        extra={
            "qc.stage": stage,
            "qc.verdict": verdict,
            "qc.preset_id": preset_id,
            "qc.preset_version": preset_version,
            "qc.run_id": run_id,
            "qc.asset_id": asset_id,
            "trace_id": trace_id,
            "span_id": span_id,
            **{f"qc.{k}": v for k, v in measurements.items()},
        },
    )


def emit_pipeline_complete(*, run_id: str, asset_id: str, stage_count: int) -> None:
    """Watermark marking a run's telemetry as fully emitted.

    Per-signal ingestion is independent, so this is a hint rather than a promise:
    a consumer should still poll for the specific spans and log lines it expects
    rather than treating this line as proof they are all queryable.
    """
    if not enabled():
        return
    trace_id, span_id = current_trace_ids()
    logging.getLogger(LOGGER_NAME).info(
        "pipeline.completed run=%s stages=%d",
        run_id,
        stage_count,
        extra={
            "qc.event": "pipeline.completed",
            "qc.run_id": run_id,
            "qc.asset_id": asset_id,
            "qc.stage_count": stage_count,
            "trace_id": trace_id,
            "span_id": span_id,
        },
    )
