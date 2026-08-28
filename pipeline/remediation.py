"""Allowlisted repair execution and re-validation.

Execution authority lives HERE, not in the agent. The agent may only name an
action id and supply parameters; this module re-validates that request against the
allowlist before running anything, and never accepts a command string.

Two properties that matter:

  * Repairs write a NEW artefact and never overwrite the input. That mirrors GCS
    generation semantics and means a bad repair cannot destroy evidence.
  * Re-validation uses THE SAME policy engine that blocked the asset. The code
    that says "no" is the code that says "yes", which is what makes the outcome
    falsifiable rather than self-reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.evidence import ProposedAction, validate_action

from .ffmpeg import loudnorm_filter, transcode_audio
from .policy import PASS, Profile, Verdict, evaluate
from .qc import run_qc

REENCODE_WITH_PROFILE = "reencode_with_profile"
REENCODE_WITH_LOUDNESS_TARGET = "reencode_with_loudness_target"
ESCALATE_TO_HUMAN = "escalate_to_human"

# Which normalisation each shipped delivery profile implies.
_PROFILE_TARGETS = {
    "ebu-r128-tv": -23.0,
    "atsc-a85-tv": -24.0,
}


@dataclass(frozen=True)
class RepairResult:
    action_id: str
    executed: bool
    input_path: str
    output_path: str | None
    verdict: Verdict | None
    message: str

    @property
    def resolved(self) -> bool:
        return self.verdict is not None and self.verdict.status == PASS

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "executed": self.executed,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "resolved": self.resolved,
            "message": self.message,
            "verdict": self.verdict.to_dict() if self.verdict else None,
        }


class RemediationError(RuntimeError):
    pass


def execute_repair(
    action_id: str,
    params: dict,
    *,
    source_path: str | Path,
    profile: Profile,
    out_dir: str | Path,
    allowlist: dict[str, dict],
) -> RepairResult:
    """Re-validate the request, execute it, then re-run the SAME gate.

    `source_path` should be the stage INPUT that was still in spec - repairing the
    already-damaged delivered file would bake the defect in.
    """
    errors = validate_action(
        ProposedAction(action_id=action_id, params=params, rationale="executor check"),
        allowlist,
    )
    if errors:
        raise RemediationError("; ".join(errors))

    src = Path(source_path)
    if not src.exists():
        raise RemediationError(f"source not found: {src}")

    if action_id == ESCALATE_TO_HUMAN:
        return RepairResult(
            action_id=action_id,
            executed=False,
            input_path=str(src),
            output_path=None,
            verdict=None,
            message=f"escalated: {params.get('reason', '')}",
        )

    if action_id == REENCODE_WITH_PROFILE:
        profile_id = params["profile_id"]
        if profile_id not in _PROFILE_TARGETS:
            raise RemediationError(f"no normalisation defined for profile {profile_id!r}")
        target = _PROFILE_TARGETS[profile_id]
    elif action_id == REENCODE_WITH_LOUDNESS_TARGET:
        target = float(params["target_lufs"])
    else:
        raise RemediationError(f"executor has no implementation for {action_id!r}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Never overwrite: a new generation each time.
    n = len(list(out.glob(f"{src.stem}_repair_*.mp4"))) + 1
    dst = out / f"{src.stem}_repair_{n:02d}.mp4"

    transcode_audio(src, dst, loudnorm_filter(target))

    report = run_qc(dst, black_opts=profile.black_detector_opts)
    verdict = evaluate(profile, report)

    return RepairResult(
        action_id=action_id,
        executed=True,
        input_path=str(src),
        output_path=str(dst),
        verdict=verdict,
        message=(
            f"re-encoded toward {target} LUFS; re-validated by the same policy "
            f"engine -> {verdict.status}"
        ),
    )
