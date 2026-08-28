"""Thin, single-purpose wrappers around ffmpeg/ffprobe.

Every subprocess call in the pipeline goes through here so that argument
construction, error handling and the mandatory delivery flags live in one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Without +faststart the moov atom trails the media, so a browser can neither
# seek nor progressively play - which makes a working demo look broken on camera.
FASTSTART = ["-movflags", "+faststart"]

_BASE = ["-hide_banner", "-nostats", "-loglevel", "error"]


class FFmpegError(RuntimeError):
    """A tool exited non-zero, or produced output we could not parse."""


def run(cmd: list[str]) -> str:
    """Run a command and return combined output. ffmpeg reports on stderr."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise FFmpegError(f"{cmd[0]} exited {proc.returncode}\n{output[-2000:]}")
    return output


def analyse(src: str | Path, *, audio_filter: str = "", video_filter: str = "") -> str:
    """Decode `src` through a measuring filter, discarding output.

    Used by the QC filters (ebur128, blackdetect) which report to stderr.
    """
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", str(src)]
    if audio_filter:
        cmd += ["-af", audio_filter]
    if video_filter:
        cmd += ["-vf", video_filter]
    return run(cmd + ["-f", "null", "-"])


def probe_duration(src: str | Path) -> float:
    out = run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(src),
        ]
    ).strip()
    try:
        return float(out.splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise FFmpegError(f"could not parse duration from {out!r}") from exc


def transcode_audio(src: str | Path, dst: str | Path, audio_filter: str) -> Path:
    """Apply an audio filter, copying video through untouched.

    Every preset and every repair in this project is audio-only, so copying the
    video stream keeps runs fast and guarantees the picture cannot drift.
    """
    dst = Path(dst)
    run(
        [
            FFMPEG,
            *_BASE,
            "-y",
            "-i",
            str(src),
            "-af",
            audio_filter,
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            *FASTSTART,
            str(dst),
        ]
    )
    return dst


def loudnorm_filter(target_lufs: float, *, true_peak: float = -1.5, lra: float = 11) -> str:
    """Single-pass EBU R128 normalisation filter string."""
    return f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}"
