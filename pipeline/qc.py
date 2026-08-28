"""Real ffmpeg QC measurement.

This module ONLY measures. It never decides pass/fail - that is policy.py's job,
and keeping the two apart is the whole point: the model interprets, deterministic
code adjudicates.

Measurements come from ffmpeg filters that genuinely decode the file:
  ebur128    -> integrated loudness (LUFS) + true peak (dBTP)
  blackdetect -> black intervals as (start, end, duration)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg import FFmpegError, analyse, probe_duration

# Kept as an alias so callers have one exception type to catch across the pipeline.
QCError = FFmpegError


@dataclass(frozen=True)
class BlackInterval:
    start_s: float
    end_s: float
    duration_s: float


@dataclass(frozen=True)
class LoudnessMeasurement:
    """EBU R128 loudness. `integrated_lufs` is the summary I value.

    Deliberately NOT momentary or short-term, and NOT dialogue-gated: those are
    different measurements and cannot be compared against an R128 target.
    """

    integrated_lufs: float
    true_peak_dbtp: float | None
    threshold_lufs: float | None
    loudness_range_lu: float | None


@dataclass(frozen=True)
class QCReport:
    asset_path: str
    duration_s: float
    loudness: LoudnessMeasurement
    black_intervals: list[BlackInterval] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "asset_path": self.asset_path,
            "duration_s": self.duration_s,
            "integrated_lufs": self.loudness.integrated_lufs,
            "true_peak_dbtp": self.loudness.true_peak_dbtp,
            "loudness_range_lu": self.loudness.loudness_range_lu,
            "black_intervals": [
                {"start_s": b.start_s, "end_s": b.end_s, "duration_s": b.duration_s}
                for b in self.black_intervals
            ],
        }


# The ebur128 Summary block looks like:
#
#   Summary:
#
#     Integrated loudness:
#       I:         -23.0 LUFS
#       Threshold: -33.6 LUFS
#     Loudness range:
#       LRA:         0.0 LU
#       ...
#     True peak:
#       Peak:       -1.5 dBFS
#
# We parse the LAST occurrence of each, because -af can emit progress lines too.
_RE_I = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.M)
_RE_THRESH = re.compile(r"^\s*Threshold:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.M)
_RE_LRA = re.compile(r"^\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", re.M)
_RE_PEAK = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", re.M)


def measure_loudness(path: str | Path) -> LoudnessMeasurement:
    """Measure EBU R128 integrated loudness and true peak with a full decode pass."""
    out = analyse(path, audio_filter="ebur128=peak=true")
    m_i = _RE_I.findall(out)
    if not m_i:
        raise QCError(f"no integrated loudness in ebur128 output:\n{out[-2000:]}")

    def _last(rx: re.Pattern[str]) -> float | None:
        found = rx.findall(out)
        return float(found[-1]) if found else None

    return LoudnessMeasurement(
        integrated_lufs=float(m_i[-1]),
        true_peak_dbtp=_last(_RE_PEAK),
        threshold_lufs=_last(_RE_THRESH),
        loudness_range_lu=_last(_RE_LRA),
    )


_RE_BLACK = re.compile(
    r"black_start:\s*(\d+(?:\.\d+)?)\s+black_end:\s*(\d+(?:\.\d+)?)\s+"
    r"black_duration:\s*(\d+(?:\.\d+)?)"
)


def detect_black(
    path: str | Path,
    *,
    min_duration_s: float = 0.5,
    pixel_black_threshold: float = 0.10,
    picture_black_ratio: float = 0.98,
) -> list[BlackInterval]:
    """Find black intervals. Thresholds are PROFILE SETTINGS, not universal standards."""
    vf = (
        f"blackdetect=d={min_duration_s}"
        f":pix_th={pixel_black_threshold}"
        f":pic_th={picture_black_ratio}"
    )
    out = analyse(path, video_filter=vf)
    return [
        BlackInterval(start_s=float(a), end_s=float(b), duration_s=float(d))
        for a, b, d in _RE_BLACK.findall(out)
    ]


def run_qc(path: str | Path, *, black_opts: dict | None = None) -> QCReport:
    """Full QC pass over one asset. Two decodes: one for audio, one for video."""
    p = Path(path)
    if not p.exists():
        raise QCError(f"asset not found: {p}")
    return QCReport(
        asset_path=str(p),
        duration_s=probe_duration(p),
        loudness=measure_loudness(p),
        black_intervals=detect_black(p, **(black_opts or {})),
    )


if __name__ == "__main__":
    import json
    import sys

    for arg in sys.argv[1:]:
        print(json.dumps(run_qc(arg).to_dict(), indent=2))
