"""Evidence ledger, structured claims, and the citation validator.

THE CENTRAL DESIGN RULE
-----------------------
The controller binds provenance; the model supplies interpretation only.

`phase`, `query_used`, `query_hash`, `raw_result_ref`, `step_id`, `run_id` and
timestamps are written by the controller, which knows what query it actually ran.
The model contributes `finding` and `supports` and nothing else. Without this, a
model can emit a perfectly schema-valid evidence record citing a query it never
ran, and the entire integrity story is theatre.

HONEST LIMIT
------------
Schema validity proves SHAPE, not ENTAILMENT. "A supporting step exists" is not
"that step supports this claim." This validator catches uncited, mis-cited and
fabricated references. It does not verify that the reasoning is sound.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


class Phase(StrEnum):
    BASELINE = "BASELINE"
    DIVERGENCE = "DIVERGENCE"
    ACTOR = "ACTOR"
    CAUSE = "CAUSE"


PHASE_ORDER = [Phase.BASELINE, Phase.DIVERGENCE, Phase.ACTOR, Phase.CAUSE]


class ClaimType(StrEnum):
    SOURCE_IN_SPEC = "SOURCE_IN_SPEC"
    SOURCE_OUT_OF_SPEC = "SOURCE_OUT_OF_SPEC"
    DIVERGENCE_STAGE = "DIVERGENCE_STAGE"
    ACTOR_PRESET = "ACTOR_PRESET"
    ROOT_CAUSE = "ROOT_CAUSE"
    NO_FAULT = "NO_FAULT"


Confidence = Literal["high", "medium", "low"]


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class EvidenceStep(BaseModel, frozen=True):
    """One observation. Provenance fields are controller-bound and immutable."""

    # -- controller-bound --
    step_id: str
    run_id: str
    phase: Phase
    query_used: str
    query_hash: str
    raw_result_ref: str
    recorded_at: datetime

    # -- model-supplied --
    finding: str = Field(min_length=1, max_length=2000)
    supports: bool


class PendingObservation(BaseModel, frozen=True):
    """A query the controller has actually executed, awaiting interpretation."""

    step_id: str
    run_id: str
    phase: Phase
    query_used: str
    query_hash: str
    raw_result_ref: str
    raw_result: Any


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class EvidenceLedger:
    """Append-only evidence for one investigation run.

    The model never touches this directly - it goes through record_interpretation,
    which can only attach a finding to an observation the controller already made.
    """

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        self._steps: list[EvidenceStep] = []
        self._raw: dict[str, Any] = {}
        self._pending: PendingObservation | None = None

    # -- controller side ----------------------------------------------------

    def observe(self, phase: Phase, query: str, raw_result: Any) -> PendingObservation:
        """Record that the controller ran `query` and got `raw_result`.

        Called AFTER the query executes, so query_used cannot be a query that was
        never run.
        """
        step_id = f"step-{len(self._steps) + 1:02d}"
        ref = f"raw://{self.run_id}/{step_id}"
        self._raw[ref] = raw_result
        self._pending = PendingObservation(
            step_id=step_id,
            run_id=self.run_id,
            phase=phase,
            query_used=query,
            query_hash=_hash(query),
            raw_result_ref=ref,
            raw_result=raw_result,
        )
        return self._pending

    # -- model side ---------------------------------------------------------

    def record_interpretation(self, finding: str, supports: bool) -> EvidenceStep:
        """Attach the model's reading to the current pending observation."""
        if self._pending is None:
            raise LedgerError("no pending observation to interpret")
        step = EvidenceStep(
            **self._pending.model_dump(exclude={"raw_result"}),
            recorded_at=datetime.now(UTC),
            finding=finding,
            supports=supports,
        )
        self._steps.append(step)
        self._pending = None
        return step

    # -- reads --------------------------------------------------------------

    @property
    def steps(self) -> list[EvidenceStep]:
        return list(self._steps)

    def step_ids(self) -> set[str]:
        return {s.step_id for s in self._steps}

    def get(self, step_id: str) -> EvidenceStep | None:
        return next((s for s in self._steps if s.step_id == step_id), None)

    def steps_for(self, phase: Phase) -> list[EvidenceStep]:
        return [s for s in self._steps if s.phase == phase]

    def raw(self, ref: str) -> Any:
        return self._raw.get(ref)

    def phase_complete(self, phase: Phase) -> bool:
        """Controller-side completion check - the model does not get a vote."""
        return len(self.steps_for(phase)) == 1


class LedgerError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Claims and conclusion
# --------------------------------------------------------------------------


class Claim(BaseModel):
    claim_type: ClaimType
    claim_value: str = Field(min_length=1, max_length=500)
    supporting_step_ids: list[str] = Field(min_length=1)
    confidence: Confidence


class ProposedAction(BaseModel):
    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=1000)


class Conclusion(BaseModel):
    claims: list[Claim] = Field(min_length=1)
    proposed_action: ProposedAction | None = None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)

    def as_tool_response(self) -> dict:
        return {"ok": self.ok, "errors": self.errors}


def validate_conclusion(
    conclusion: Conclusion,
    ledger: EvidenceLedger,
    *,
    allowlist: dict[str, dict] | None = None,
) -> ValidationResult:
    """Reject uncited, mis-cited or fabricated conclusions.

    Checks, in order:
      1. every claim cites at least one step        (schema enforces min_length=1)
      2. every cited step_id EXISTS in this ledger  (catches fabricated citations)
      3. every cited step belongs to this run       (catches cross-run leakage)
      4. a proposed action is on the allowlist with valid parameters
    """
    errors: list[str] = []
    known = ledger.step_ids()

    for i, claim in enumerate(conclusion.claims):
        if not claim.supporting_step_ids:
            errors.append(f"claims[{i}] ({claim.claim_type.value}): no supporting_step_ids")
            continue
        for sid in claim.supporting_step_ids:
            if sid not in known:
                errors.append(
                    f"claims[{i}] ({claim.claim_type.value}): cites unknown step "
                    f"{sid!r}; known steps are {sorted(known) or '[none]'}"
                )
                continue
            step = ledger.get(sid)
            if step is not None and step.run_id != ledger.run_id:
                errors.append(f"claims[{i}]: step {sid!r} belongs to another run")

    if conclusion.proposed_action is not None and allowlist is not None:
        errors.extend(validate_action(conclusion.proposed_action, allowlist))

    return ValidationResult(ok=not errors, errors=errors)


def validate_action(action: ProposedAction, allowlist: dict[str, dict]) -> list[str]:
    if action.action_id not in allowlist:
        return [
            f"action {action.action_id!r} is not on the allowlist "
            f"(permitted: {sorted(allowlist)})"
        ]
    errors: list[str] = []
    spec = allowlist[action.action_id]
    for name, rule in spec.items():
        if name not in action.params:
            errors.append(f"action {action.action_id!r}: missing parameter {name!r}")
            continue
        value = action.params[name]
        if "enum" in rule and value not in rule["enum"]:
            errors.append(
                f"action {action.action_id!r}: {name}={value!r} not in {rule['enum']}"
            )
        if "min" in rule or "max" in rule:
            try:
                num = float(value)
            except (TypeError, ValueError):
                errors.append(f"action {action.action_id!r}: {name}={value!r} is not numeric")
                continue
            if "min" in rule and num < float(rule["min"]):
                errors.append(
                    f"action {action.action_id!r}: {name}={num} below min {rule['min']}"
                )
            if "max" in rule and num > float(rule["max"]):
                errors.append(
                    f"action {action.action_id!r}: {name}={num} above max {rule['max']}"
                )
        if "max_length" in rule and isinstance(value, str) and len(value) > rule["max_length"]:
            errors.append(
                f"action {action.action_id!r}: {name} exceeds {rule['max_length']} chars"
            )
    for extra in set(action.params) - set(spec):
        errors.append(f"action {action.action_id!r}: unexpected parameter {extra!r}")
    return errors


def allowlist_from_profile(profile_data: dict) -> dict[str, dict]:
    """Build the action allowlist from the delivery profile. The profile is the authority."""
    return {
        entry["id"]: entry.get("params", {})
        for entry in profile_data.get("remediation_allowlist", [])
    }


def safe_record_interpretation(ledger: EvidenceLedger, finding: str, supports: bool) -> dict:
    """Tool-facing wrapper. NEVER raises.

    ADK turns an exception raised inside a tool into an aborted invocation rather
    than a retryable tool response, so validation failures must come back as data
    the model can see and correct.
    """
    try:
        step = ledger.record_interpretation(finding=finding, supports=supports)
    except (ValidationError, LedgerError) as exc:
        return {"ok": False, "error": _compact_error(exc)}
    return {"ok": True, "step_id": step.step_id, "phase": step.phase.value}


def _compact_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
    return str(exc)


def ledger_to_json(ledger: EvidenceLedger) -> str:
    return json.dumps(
        {
            "run_id": ledger.run_id,
            "steps": [json.loads(s.model_dump_json()) for s in ledger.steps],
        },
        indent=2,
    )
