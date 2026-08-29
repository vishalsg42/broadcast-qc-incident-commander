"""Test a suspected cause by running the experiment, instead of inferring it.

The investigation can name the preset that ran and read the filter string it
applied. Reasoning from `pan=stereo|c0=c0+c1|c1=c0+c1` to "that is what raised
the loudness" is an educated guess about a string, and hedged wording is a
mitigation rather than evidence.

So run the controlled experiment the situation actually admits:

    same stage, same input, previous preset version vs the suspect one.

`pkg_h264_v6` is `anull`; `pkg_h264_v7` applies the downmix. Feed both the
*identical* input the failing stage consumed and measure the results. That is an
A/B differential on the real artefact, not a filter applied to a proxy, and the
control rules out the input being the cause.

WHAT THIS DOES AND DOES NOT ESTABLISH
-------------------------------------
It establishes that, on this input, the suspect preset produces a
non-compliant result where the control preset does not. That is real,
reproducible and falsifiable.

It does NOT establish sole causation - it cannot exclude a second, concurrent
fault - and the measured delta is a property of THIS CONTENT, not of the preset.
Summing identical channels gives exactly +6.02 dB; decorrelated stereo gives
roughly +3 LU; a hard-panned mix gives close to nothing. Callers must report the
delta as what happened to this asset, never as a characteristic of the preset.

The model never supplies a filter string. Callers resolve presets from the
library, because an arbitrary filtergraph reaching ffmpeg is a file-write
primitive (`ametadata=...:file=`), not merely a computation.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import transcode_audio
from .policy import Profile, evaluate
from .qc import QCError, run_qc

# ebur128's absolute gate. A window with nothing above it returns exactly this,
# and it parses as an ordinary float - so a measurement over silence, past the
# end of a file, or on a truncated asset yields a perfectly well-formed -70.0
# and a delta of 0.0. Reporting that as "the filter had no effect" would be
# fabricated evidence, so it is refused instead.
GATE_FLOOR_LUFS = -69.0


class ExperimentError(RuntimeError):
    """The experiment could not produce a measurement worth reporting."""


@dataclass(frozen=True)
class Arm:
    """One side of the comparison."""

    preset_id: str
    preset_version: int
    integrated_lufs: float
    verdict: str


@dataclass(frozen=True)
class ExperimentResult:
    input_path: str
    stage: str
    control: Arm
    suspect: Arm
    delta_lu: float
    """Suspect minus control. Positive means the suspect preset raised loudness."""

    @property
    def reproduces_defect(self) -> bool:
        """The suspect fails the same profile the control passes, from one input."""
        return self.control.verdict != "BLOCKED" and self.suspect.verdict == "BLOCKED"

    def to_dict(self) -> dict:
        return {
            "input_path": self.input_path,
            "stage": self.stage,
            "control_preset_id": self.control.preset_id,
            "control_preset_version": self.control.preset_version,
            "control_lufs": self.control.integrated_lufs,
            "control_verdict": self.control.verdict,
            "suspect_preset_id": self.suspect.preset_id,
            "suspect_preset_version": self.suspect.preset_version,
            "suspect_lufs": self.suspect.integrated_lufs,
            "suspect_verdict": self.suspect.verdict,
            "delta_lu": self.delta_lu,
            "reproduces_defect": self.reproduces_defect,
            # Stated in the payload, not left to the caller to remember.
            "caveat": (
                "The delta is a property of this content, not of the preset. "
                "Identical channels sum to +6.02 dB; decorrelated stereo is "
                "nearer +3 LU. This shows the suspect preset reproduces the "
                "defect on this input; it does not exclude a second fault."
            ),
        }


def compare_presets(
    *,
    input_path: str | Path,
    stage: str,
    control_preset,
    suspect_preset,
    profile: Profile,
    black_opts: dict | None = None,
) -> ExperimentResult:
    """Run one input through two preset versions of the same stage and measure both.

    Presets are `pipeline.stages.Preset` objects, resolved by the caller from the
    library - never a filter string from a model.

    Everything is written under a temporary directory that is removed even when
    the experiment fails, so nothing lands beside the real deliverables and
    nothing accumulates on a read-only-except-/tmp filesystem where /tmp is RAM.
    """
    src = Path(input_path)
    if not src.exists():
        raise ExperimentError(f"experiment input not found: {src}")

    with tempfile.TemporaryDirectory(prefix="qcic-experiment-") as tmp:
        arms = []
        for preset in (control_preset, suspect_preset):
            dst = Path(tmp) / f"{preset.id}{src.suffix}"
            try:
                transcode_audio(src, dst, preset.audio_filter)
                # Whole file, deliberately. A short window would save under a
                # second on a 45s asset and buys a silent failure mode: land in
                # head black and the measurement is the gate floor.
                report = run_qc(dst, black_opts=black_opts)
            except QCError as exc:
                raise ExperimentError(
                    f"experiment arm {preset.id} failed to measure: {exc}"
                ) from exc

            lufs = report.loudness.integrated_lufs
            if lufs <= GATE_FLOOR_LUFS:
                raise ExperimentError(
                    f"arm {preset.id} measured {lufs} LUFS, at or below the "
                    f"{GATE_FLOOR_LUFS} gate floor - the input is silent or "
                    "unmeasurable, so any delta would be meaningless"
                )
            arms.append(
                Arm(
                    preset_id=preset.id,
                    preset_version=preset.version,
                    integrated_lufs=lufs,
                    verdict=evaluate(profile, report).status,
                )
            )

    control, suspect = arms
    return ExperimentResult(
        input_path=str(src),
        stage=stage,
        control=control,
        suspect=suspect,
        delta_lu=round(suspect.integrated_lufs - control.integrated_lufs, 2),
    )


__all__ = [
    "GATE_FLOOR_LUFS",
    "Arm",
    "ExperimentError",
    "ExperimentResult",
    "compare_presets",
]
