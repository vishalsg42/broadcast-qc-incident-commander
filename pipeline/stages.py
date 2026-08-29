"""The three-stage delivery pipeline: ingest -> normalize -> package.

Three stages standing in for a nine-stage real workflow (conform, colour, mix,
mastering, versioning, transcode, wrap, package, deliver). The reduction is
deliberate and stated rather than hidden.

Each stage runs real ffmpeg, measures the result with real QC, and records which
PRESET VERSION it used. That last part is the point: when the delivered asset
fails, the investigation has to find which stage diverged and which preset was
responsible - and the preset is the thing a real facility would actually hunt for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import telemetry
from .ffmpeg import transcode_audio
from .policy import Profile, evaluate
from .qc import QCError, QCReport, run_qc

PRESETS_PATH = Path(__file__).parent / "presets.yaml"

INGEST = "ingest"
NORMALIZE = "normalize"
PACKAGE = "package"
STAGE_ORDER = [INGEST, NORMALIZE, PACKAGE]


@dataclass(frozen=True)
class Preset:
    id: str
    version: int
    changed_at: str
    description: str
    audio_filter: str
    stage: str
    # Change provenance. A delivery preset change is a controlled change, so
    # "who, under what ticket, approved by whom" travels with the version.
    # Optional because a preset with no recorded provenance is a real state -
    # and reporting that gap honestly beats inventing a value for it.
    changed_by: str | None = None
    change_ticket: str | None = None
    approved_by: str | None = None


# Ingest admits the source unchanged; it exists so the source is measured
# as-received, which is what lets BASELINE rule the supplier in or out.
INGEST_PRESET = Preset(
    id="ingest_passthrough_v1",
    version=1,
    changed_at="2026-01-01T00:00:00Z",
    description="Admit source unchanged; measure as received",
    audio_filter="anull",
    stage=INGEST,
    changed_by="system",
    change_ticket="n/a",
    approved_by="system",
)


class PresetLibrary:
    def __init__(self, data: dict):
        self._by_stage: dict[str, list[Preset]] = {}
        for stage, entries in data.items():
            self._by_stage[stage] = [
                Preset(
                    id=e["id"],
                    version=int(e["version"]),
                    changed_at=e["changed_at"],
                    description=e.get("description", "").strip(),
                    audio_filter=e["audio_filter"],
                    stage=stage,
                    changed_by=e.get("changed_by"),
                    change_ticket=e.get("change_ticket"),
                    approved_by=e.get("approved_by"),
                )
                for e in entries
            ]
        self._defaults = {
            stage: next(
                (
                    p
                    for p, e in zip(self._by_stage[stage], data[stage], strict=True)
                    if e.get("default")
                ),
                self._by_stage[stage][0],
            )
            for stage in self._by_stage
        }

    @classmethod
    def load(cls, path: str | Path = PRESETS_PATH) -> PresetLibrary:
        with Path(path).open() as fh:
            return cls(yaml.safe_load(fh))

    def default_for(self, stage: str) -> Preset:
        return self._defaults[stage]

    def get(self, stage: str, preset_id: str) -> Preset:
        for p in self._by_stage.get(stage, []):
            if p.id == preset_id:
                return p
        raise KeyError(f"no preset {preset_id!r} for stage {stage!r}")

    def all_for(self, stage: str) -> list[Preset]:
        return list(self._by_stage.get(stage, []))


@dataclass
class StageResult:
    stage: str
    preset: Preset
    input_path: str
    output_path: str
    qc: QCReport
    span_id: str
    started_at: str
    ended_at: str

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "preset_id": self.preset.id,
            "preset_version": self.preset.version,
            "preset_changed_at": self.preset.changed_at,
            "preset_changed_by": self.preset.changed_by,
            "preset_change_ticket": self.preset.change_ticket,
            "preset_approved_by": self.preset.approved_by,
            "span_id": self.span_id,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "qc": self.qc.to_dict(),
        }


@dataclass
class PipelineRun:
    run_id: str
    asset_id: str
    source_path: str
    stages: list[StageResult] = field(default_factory=list)

    @property
    def delivered_path(self) -> str:
        return self.stages[-1].output_path

    def stage(self, name: str) -> StageResult | None:
        return next((s for s in self.stages if s.stage == name), None)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "asset_id": self.asset_id,
            "source_path": self.source_path,
            "stages": [s.to_dict() for s in self.stages],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def run_pipeline(
    source: str | Path,
    *,
    out_dir: str | Path,
    presets: PresetLibrary | None = None,
    overrides: dict[str, str] | None = None,
    black_opts: dict | None = None,
    asset_id: str | None = None,
    profile: Profile | None = None,
) -> PipelineRun:
    """Run ingest -> normalize -> package, measuring QC after every stage.

    `overrides` maps stage -> preset_id and is how a fault is injected: it is
    ordinary configuration, not a special case buried in the code.

    `profile` is optional and only used to label emitted telemetry with a verdict;
    it never changes what the pipeline produces.
    """
    src = Path(source)
    if not src.exists():
        raise QCError(f"source not found: {src}")

    presets = presets or PresetLibrary.load()
    overrides = overrides or {}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    run = PipelineRun(
        run_id=f"run-{uuid.uuid4().hex[:12]}",
        asset_id=asset_id or src.stem,
        source_path=str(src),
    )

    current = src
    # Every stage span must be a child of this one, or Tempo shows three
    # unrelated single-span traces instead of the asset's journey.
    with telemetry.run_span(run_id=run.run_id, asset_id=run.asset_id, source_path=str(src)):
        for stage in STAGE_ORDER:
            preset, dst = _resolve_stage(stage, run, presets, overrides, out, current)
            result = _execute_stage(stage, preset, current, dst, run, black_opts, profile)
            run.stages.append(result)
            current = Path(result.output_path)

        telemetry.emit_pipeline_complete(
            run_id=run.run_id, asset_id=run.asset_id, stage_count=len(run.stages)
        )

    return run


def _resolve_stage(
    stage: str,
    run: PipelineRun,
    presets: PresetLibrary,
    overrides: dict[str, str],
    out: Path,
    current: Path,
) -> tuple[Preset, Path]:
    """Pick the preset for a stage and decide where its output goes."""
    if stage == INGEST:
        # Ingest does not transform - it admits the source and measures it.
        return INGEST_PRESET, current
    preset_id = overrides.get(stage)
    preset = presets.get(stage, preset_id) if preset_id else presets.default_for(stage)
    return preset, out / f"{run.run_id}_{stage}.mp4"


def _execute_stage(
    stage: str,
    preset: Preset,
    src: Path,
    dst: Path,
    run: PipelineRun,
    black_opts: dict | None,
    profile: Profile | None,
) -> StageResult:
    """Transcode (unless ingest), measure, and emit telemetry for one stage."""
    started = _now()
    with telemetry.stage_span(
        stage,
        preset_id=preset.id,
        preset_version=preset.version,
        preset_changed_at=preset.changed_at,
        preset_changed_by=preset.changed_by,
        preset_change_ticket=preset.change_ticket,
        preset_approved_by=preset.approved_by,
        run_id=run.run_id,
        asset_id=run.asset_id,
    ):
        if stage != INGEST:
            transcode_audio(src, dst, preset.audio_filter)
        report = run_qc(dst, black_opts=black_opts)

        telemetry.emit_qc_observation(
            stage=stage,
            run_id=run.run_id,
            asset_id=run.asset_id,
            preset_id=preset.id,
            preset_version=preset.version,
            verdict=evaluate(profile, report).status if profile else "UNKNOWN",
            measurements={
                "integrated_lufs": report.loudness.integrated_lufs,
                "true_peak_dbtp": report.loudness.true_peak_dbtp,
                "black_interval_count": len(report.black_intervals),
                "duration_s": report.duration_s,
            },
        )
        _, span_id = telemetry.current_trace_ids()

    return StageResult(
        stage=stage,
        preset=preset,
        input_path=str(src),
        output_path=str(dst),
        qc=report,
        span_id=span_id or uuid.uuid4().hex[:16],
        started_at=started,
        ended_at=_now(),
    )


if __name__ == "__main__":
    import json
    import sys

    from .policy import Profile, evaluate

    prof = Profile.load(Path(__file__).parent / "profiles" / "ebu_r128.yaml")
    src = sys.argv[1]
    override = {}
    if len(sys.argv) > 2:
        override[PACKAGE] = sys.argv[2]

    run = run_pipeline(
        src, out_dir="out", overrides=override, black_opts=prof.black_detector_opts
    )
    print(json.dumps(run.to_dict(), indent=2))
    print()
    for s in run.stages:
        v = evaluate(prof, s.qc)
        print(
            f"{s.stage:10s} preset={s.preset.id:24s} "
            f"I={s.qc.loudness.integrated_lufs:7.1f} LUFS  -> {v.status}"
        )
