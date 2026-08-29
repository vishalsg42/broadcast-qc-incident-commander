"""The differential experiment: test the suspected cause instead of inferring it.

The investigation can read a preset's filter string and reason that it probably
raised the loudness. These tests cover the alternative: run the same input
through the previous preset version and the suspect one, and measure both.

The media-marked tests shell out to real ffmpeg, because an experiment verified
against a mock proves nothing about whether the experiment works.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from pipeline.experiment import (
    Arm,
    ExperimentError,
    ExperimentResult,
    compare_presets,
)
from pipeline.policy import load_profile
from pipeline.stages import PACKAGE, PresetLibrary

MEDIA = Path(__file__).parent.parent / "media"


def _arm(preset_id: str, lufs: float, verdict: str) -> Arm:
    return Arm(preset_id=preset_id, preset_version=1, integrated_lufs=lufs, verdict=verdict)


def _result(control_verdict: str, suspect_verdict: str) -> ExperimentResult:
    return ExperimentResult(
        input_path="x.mp4",
        stage=PACKAGE,
        control=_arm("v6", -22.8, control_verdict),
        suspect=_arm("v7", -16.8, suspect_verdict),
        delta_lu=6.0,
    )


class TestReproducesDefect:
    def test_true_only_when_control_passes_and_suspect_blocks(self):
        assert _result("PASS", "BLOCKED").reproduces_defect is True

    def test_false_when_both_block(self):
        """If the control also fails, the input is the problem, not the preset."""
        assert _result("BLOCKED", "BLOCKED").reproduces_defect is False

    def test_false_when_suspect_passes(self):
        assert _result("PASS", "PASS").reproduces_defect is False

    def test_false_when_the_profile_could_not_be_measured(self):
        assert _result("UNMEASURABLE", "UNMEASURABLE").reproduces_defect is False

    def test_payload_carries_the_content_dependence_caveat(self):
        """The delta must never travel without it."""
        d = _result("PASS", "BLOCKED").to_dict()
        assert "property of this content" in d["caveat"]
        assert "+6.02" in d["caveat"]


class TestRefusals:
    def test_missing_input_is_refused(self):
        lib = PresetLibrary.load()
        with pytest.raises(ExperimentError, match="not found"):
            compare_presets(
                input_path="/nonexistent/nope.mp4",
                stage=PACKAGE,
                control_preset=lib.get(PACKAGE, "pkg_h264_v6"),
                suspect_preset=lib.get(PACKAGE, "pkg_h264_v7"),
                profile=load_profile("ebu-r128-tv"),
            )


@pytest.mark.media
class TestAgainstRealFfmpeg:
    def test_the_suspect_preset_reproduces_the_defect(self, tmp_path):
        """Same input, two preset versions: one passes, one blocks."""
        profile = load_profile("ebu-r128-tv")
        lib = PresetLibrary.load()
        res = compare_presets(
            input_path=MEDIA / "master_good.mp4",
            stage=PACKAGE,
            control_preset=lib.get(PACKAGE, "pkg_h264_v6"),
            suspect_preset=lib.get(PACKAGE, "pkg_h264_v7"),
            profile=profile,
            black_opts=profile.black_detector_opts,
        )
        assert res.control.verdict == "PASS"
        assert res.suspect.verdict == "BLOCKED"
        assert res.reproduces_defect is True
        assert res.delta_lu > 0

    def test_measurement_over_silence_is_refused_not_reported_as_no_effect(self, tmp_path):
        """The failure mode that would fabricate evidence.

        ebur128 returns exactly -70.0 for a gated-out window and it parses as an
        ordinary float, so a silent input yields before == after and a delta of
        0.0 - which reads as "the filter did nothing". It is refused instead.
        """
        silent = tmp_path / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25:d=5",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo:d=5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(silent),
            ],
            check=True,
            capture_output=True,
        )
        lib = PresetLibrary.load()
        with pytest.raises(ExperimentError, match="gate floor"):
            compare_presets(
                input_path=silent,
                stage=PACKAGE,
                control_preset=lib.get(PACKAGE, "pkg_h264_v6"),
                suspect_preset=lib.get(PACKAGE, "pkg_h264_v7"),
                profile=load_profile("ebu-r128-tv"),
            )

    def test_temp_files_are_removed_even_when_the_experiment_fails(self, tmp_path):
        """Cleanup on the failing path, not just the happy one.

        On Cloud Run /tmp is RAM against the instance memory limit, and the
        instance is long-lived.
        """
        silent = tmp_path / "silent.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25:d=5",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo:d=5",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(silent),
            ],
            check=True,
            capture_output=True,
        )
        tmp_root = Path(tempfile.gettempdir())
        before = set(tmp_root.glob("qcic-experiment-*"))
        lib = PresetLibrary.load()
        with pytest.raises(ExperimentError):
            compare_presets(
                input_path=silent,
                stage=PACKAGE,
                control_preset=lib.get(PACKAGE, "pkg_h264_v6"),
                suspect_preset=lib.get(PACKAGE, "pkg_h264_v7"),
                profile=load_profile("ebu-r128-tv"),
            )
        assert set(tmp_root.glob("qcic-experiment-*")) == before

    def test_nothing_is_written_beside_the_deliverables(self, tmp_path):
        """The experiment must never land a file in the output directory."""
        out = tmp_path / "out"
        out.mkdir()
        profile = load_profile("ebu-r128-tv")
        lib = PresetLibrary.load()
        compare_presets(
            input_path=MEDIA / "master_good.mp4",
            stage=PACKAGE,
            control_preset=lib.get(PACKAGE, "pkg_h264_v6"),
            suspect_preset=lib.get(PACKAGE, "pkg_h264_v7"),
            profile=profile,
            black_opts=profile.black_detector_opts,
        )
        assert list(out.iterdir()) == []

    def test_the_delta_depends_on_the_content_not_the_preset(self, tmp_path):
        """The honesty requirement, asserted rather than only documented.

        The shipped fixture is a mono tone upmixed by duplication, so L and R are
        bit-identical and summing them gives exactly +6.02 dB. Decorrelated
        stereo gives a visibly smaller rise through the SAME preset - so the
        number can never be quoted as a property of pkg_h264_v7.
        """
        decorrelated = tmp_path / "decorrelated.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:r=25:d=20",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=20",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=997:sample_rate=48000:duration=20",
                "-filter_complex",
                "[1:a][2:a]amerge=inputs=2[a]",
                "-map",
                "0:v",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(decorrelated),
            ],
            check=True,
            capture_output=True,
        )
        profile = load_profile("ebu-r128-tv")
        lib = PresetLibrary.load()
        kw = {
            "stage": PACKAGE,
            "control_preset": lib.get(PACKAGE, "pkg_h264_v6"),
            "suspect_preset": lib.get(PACKAGE, "pkg_h264_v7"),
            "profile": profile,
        }
        identical = compare_presets(input_path=MEDIA / "master_good.mp4", **kw)
        decorr = compare_presets(input_path=decorrelated, **kw)

        assert identical.delta_lu == pytest.approx(6.0, abs=0.3)
        assert decorr.delta_lu < identical.delta_lu - 1.0, (
            f"expected a smaller rise on decorrelated content, got "
            f"{decorr.delta_lu} vs {identical.delta_lu}"
        )
