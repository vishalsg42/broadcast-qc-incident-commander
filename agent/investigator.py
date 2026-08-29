"""Investigation controller.

Four phases, in fixed order, each one query:

  BASELINE    was the source in spec when it arrived?     Loki
  DIVERGENCE  which stage did it first fail at?           Loki
  ACTOR       which preset version ran that stage?        Tempo
  CAUSE       what about that preset explains it?         Loki

**The controller gathers. The model interprets.** Every `gather_*` function runs
its own query and hands the result to `EvidenceLedger.observe()`, which binds the
phase, the query text, its hash, and a reference to the raw response. The model
then calls `record_evidence(finding, supports)` and can contribute nothing else.
That is what stops a model producing a schema-valid evidence record citing a
query it never ran.

Retrieval being deterministic is deliberate, not a shortcut. "Fetch the ingest QC
line for this run" needs no model, and pretending otherwise would be theatre. The
model's job is judgement: what each result means, whether it supports the
hypothesis, and what to propose - with every claim cited back to a step the
controller recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pipeline.policy import BLOCKED
from pipeline.stages import INGEST, STAGE_ORDER

from .evidence import EvidenceLedger, Phase
from .grafana import GrafanaClient, GrafanaError

# Query templates. The model supplies parameters derived from earlier results; it
# never authors query text, and the controller records exactly what ran.
Q_BASELINE = '{{service_name="qc-pipeline"}} | qc_run_id="{run_id}" | qc_stage="{stage}"'
Q_ALL_STAGES = '{{service_name="qc-pipeline"}} | qc_run_id="{run_id}" | qc_stage=~".+"'
# TraceQL: dotted attribute names need the UNSCOPED form with a leading dot.
# `span.qc.run_id` is parsed as scope `span` + `.qc.run_id` and errors with
# "unknown identifier: span"; `span["qc.run_id"]` is not supported either.
Q_TRACE_SEARCH = '{{name="delivery.run" && .qc.run_id="{run_id}"}}'
Q_PRESET_LOGS = '{{service_name="qc-pipeline"}} | qc_preset_id="{preset_id}"'


class InvestigationError(RuntimeError):
    pass


@dataclass
class PhaseResult:
    """What the controller retrieved for one phase, before interpretation."""

    phase: Phase
    query: str
    raw: Any
    summary: dict
    """Model-facing digest. Small on purpose - the raw payload stays in the
    ledger, referenced by `raw_result_ref`, rather than being pasted into a
    prompt where it would be summarised away."""


class Investigator:
    """Runs the fixed investigation topology for one blocked delivery."""

    def __init__(
        self,
        client: GrafanaClient,
        ledger: EvidenceLedger,
        *,
        run_id: str,
        asset_id: str,
        lookback_s: int = 3600,
        trace_timeout_s: float = 120.0,
    ):
        self.client = client
        self.ledger = ledger
        self.run_id = run_id
        self.asset_id = asset_id
        self.lookback_s = lookback_s
        self.trace_timeout_s = trace_timeout_s

    # -- phase 1 ------------------------------------------------------------

    def gather_baseline(self) -> PhaseResult:
        """Was the source in spec on arrival?

        If it was not, the correct answer is to reject the source - re-encoding
        our way out of a supplier problem is the wrong action, and the
        investigation should terminate rather than hunt for a pipeline fault.
        """
        query = Q_BASELINE.format(run_id=self.run_id, stage=INGEST)
        entries = self.client.query_logs(query, lookback_s=self.lookback_s, limit=20)
        if not entries:
            raise InvestigationError(f"no ingest observation for run {self.run_id}")

        entry = entries[0]
        summary = {
            "stage": INGEST,
            "verdict": entry.label("qc_verdict"),
            "integrated_lufs": _as_float(entry.label("qc_integrated_lufs")),
            "source_in_spec": entry.label("qc_verdict") != BLOCKED,
        }
        self.ledger.observe(Phase.BASELINE, query, [_entry_dict(e) for e in entries])
        return PhaseResult(Phase.BASELINE, query, entries, summary)

    # -- phase 2 ------------------------------------------------------------

    def gather_divergence(self) -> PhaseResult:
        """Which stage did the asset first fall out of spec at?"""
        query = Q_ALL_STAGES.format(run_id=self.run_id)
        entries = self.client.query_logs(query, lookback_s=self.lookback_s, limit=50)
        if not entries:
            raise InvestigationError(f"no stage observations for run {self.run_id}")

        by_stage = {}
        for entry in entries:
            stage = entry.label("qc_stage")
            if stage and stage not in by_stage:
                by_stage[stage] = entry

        ordered = [(s, by_stage[s]) for s in STAGE_ORDER if s in by_stage]
        first_bad = next((s for s, e in ordered if e.label("qc_verdict") == BLOCKED), None)
        last_good = None
        for stage, entry in ordered:
            if entry.label("qc_verdict") == BLOCKED:
                break
            last_good = stage

        summary = {
            "stages": [
                {
                    "stage": s,
                    "verdict": e.label("qc_verdict"),
                    "integrated_lufs": _as_float(e.label("qc_integrated_lufs")),
                    "preset_id": e.label("qc_preset_id"),
                }
                for s, e in ordered
            ],
            "first_failing_stage": first_bad,
            "last_good_stage": last_good,
        }
        self.ledger.observe(Phase.DIVERGENCE, query, [_entry_dict(e) for e in entries])
        return PhaseResult(Phase.DIVERGENCE, query, entries, summary)

    # -- phase 3 ------------------------------------------------------------

    def gather_actor(self, failing_stage: str) -> PhaseResult:
        """Which preset version ran the failing stage?

        Comes from the trace, not the logs, so the preset attribution is
        genuinely load-bearing on Tempo rather than decorative.
        """
        search = Q_TRACE_SEARCH.format(run_id=self.run_id)
        # Tempo ingestion lags span export by 30s-2min, so a single query right
        # after a run reads as "no data" rather than "not yet".
        traces = self.client.wait_for_traces(
            search, lookback_s=self.lookback_s, timeout_s=self.trace_timeout_s
        )

        trace_id = traces[0]["traceID"]
        query = f"{search}  -> trace {trace_id}"
        spans = self.client.get_trace(trace_id)

        target = next(
            (s for s in spans if s.attributes.get("qc.stage") == failing_stage), None
        )
        if target is None:
            raise InvestigationError(
                f"trace {trace_id} has no span for stage {failing_stage!r}"
            )

        changed_at = target.attributes.get("qc.preset_changed_at")
        summary = {
            "trace_id": trace_id,
            "stage": failing_stage,
            "preset_id": target.attributes.get("qc.preset_id"),
            "preset_version": target.attributes.get("qc.preset_version"),
            "preset_changed_at": changed_at,
            # WHICH preset ran and WHETHER it changed recently are independent
            # facts. Fusing them tells one causal story - "a preset changed" -
            # and the more common real fault is a preset that changed nothing
            # and was MIS-SELECTED: a stereo title routed through the 5.1
            # fold-down profile. Same symptom, same filter, changed_at from
            # eight months ago.
            "recently_changed": _changed_recently(changed_at),
            "days_since_change": _days_since(changed_at),
            "sibling_stages": [
                {
                    "stage": s.attributes.get("qc.stage"),
                    "preset_id": s.attributes.get("qc.preset_id"),
                }
                for s in spans
                if s.attributes.get("qc.stage") and s is not target
            ],
        }
        self.ledger.observe(
            Phase.ACTOR,
            query,
            {"trace_id": trace_id, "spans": [_span_dict(s) for s in spans]},
        )
        return PhaseResult(Phase.ACTOR, query, spans, summary)

    # -- phase 4 ------------------------------------------------------------

    def gather_cause(self, preset_id: str, preset_definition: dict) -> PhaseResult:
        """What about that preset explains the divergence?

        A preset appearing shortly before a failure is CORRELATION. The preset
        definition is included so a causal claim can rest on what the preset
        actually does, not merely on when it changed.
        """
        query = Q_PRESET_LOGS.format(preset_id=preset_id)
        try:
            entries = self.client.query_logs(query, lookback_s=self.lookback_s, limit=50)
        except GrafanaError:
            entries = []

        summary = {
            "preset_id": preset_id,
            "audio_filter": preset_definition.get("audio_filter"),
            "description": preset_definition.get("description"),
            "changed_at": preset_definition.get("changed_at"),
            "observations_with_this_preset": len(entries),
            "verdicts_seen": sorted(
                {e.label("qc_verdict") for e in entries if e.label("qc_verdict")}
            ),
        }
        self.ledger.observe(
            Phase.CAUSE,
            query,
            {
                "preset_definition": preset_definition,
                "observations": [_entry_dict(e) for e in entries],
            },
        )
        return PhaseResult(Phase.CAUSE, query, entries, summary)


RECENT_CHANGE_DAYS = 7


def _days_since(changed_at: str | None) -> float | None:
    if not changed_at:
        return None
    try:
        when = datetime.fromisoformat(str(changed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (datetime.now(UTC) - when).total_seconds() / 86400


def _changed_recently(changed_at: str | None) -> bool | None:
    """Whether the preset changed recently enough to be a plausible trigger.

    None when unknown. A preset that has not changed in months did not cause
    today's failure by changing - it may still be the wrong preset for this
    title, which is a different finding.
    """
    days = _days_since(changed_at)
    return None if days is None else days <= RECENT_CHANGE_DAYS


def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry_dict(entry) -> dict:
    return {
        "timestamp_ns": entry.timestamp_ns,
        "line": entry.line,
        "labels": entry.labels,
    }


def _span_dict(span) -> dict:
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "attributes": span.attributes,
    }
