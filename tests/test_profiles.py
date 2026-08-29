"""Delivery profiles, and the gate's refusal to adjudicate what it cannot measure.

The Netflix case is the important one. Its published target is -27 LKFS
DIALOG-GATED, and ffmpeg's `ebur128` implements R128 gating, not dialogue
gating. Comparing one against the other compares two different quantities, so
the gate must decline. A wrong verdict delivered confidently is worse than no
verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.policy import BLOCKED, PASS, UNMEASURABLE, Profile, evaluate
from pipeline.qc import BlackInterval, LoudnessMeasurement, QCReport

PROFILES = Path(__file__).parent.parent / "pipeline" / "profiles"


def _report(lufs: float = -23.0, true_peak: float = -3.0) -> QCReport:
    return QCReport(
        asset_path="synthetic",
        duration_s=45.0,
        loudness=LoudnessMeasurement(lufs, true_peak, -33.0, 1.3),
        black_intervals=[BlackInterval(0.0, 10.0, 10.0), BlackInterval(43.0, 44.96, 1.96)],
    )


def load(name: str) -> Profile:
    return Profile.load(PROFILES / name)


class TestShippedProfiles:
    @pytest.mark.parametrize(
        "filename,profile_id",
        [
            ("ebu_r128.yaml", "ebu-r128-tv"),
            ("atsc_a85.yaml", "atsc-a85-tv"),
            ("netflix.yaml", "netflix-dialog-gated"),
        ],
    )
    def test_every_profile_loads_and_names_itself(self, filename, profile_id):
        p = load(filename)
        assert p.id == profile_id
        assert p.name and p.standard

    def test_each_declares_the_measurement_it_needs(self):
        assert load("ebu_r128.yaml").required_measurement == "bs1770_gated"
        assert load("atsc_a85.yaml").required_measurement == "bs1770_gated"
        assert load("netflix.yaml").required_measurement == "bs1770_dialog_gated"


class TestMeasurableProfiles:
    def test_r128_targets_minus_23(self):
        target, tol = load("ebu_r128.yaml").loudness_target
        assert (target, tol) == (-23.0, 0.5)

    def test_a85_targets_minus_24_with_wider_tolerance(self):
        target, tol = load("atsc_a85.yaml").loudness_target
        assert (target, tol) == (-24.0, 2.0)

    def test_same_asset_can_pass_one_profile_and_fail_another(self):
        """-22.0 is inside A/85's +/-2 window and outside R128's +/-0.5."""
        report = _report(lufs=-22.0)
        assert evaluate(load("atsc_a85.yaml"), report).status == PASS
        assert evaluate(load("ebu_r128.yaml"), report).status == BLOCKED

    def test_a85_true_peak_ceiling_is_stricter(self):
        assert load("atsc_a85.yaml").true_peak_ceiling == -2.0
        assert load("ebu_r128.yaml").true_peak_ceiling == -1.0


class TestUnmeasurableProfileIsRefused:
    """Declining is the correct answer, not a limitation to paper over."""

    def test_netflix_is_not_measurable_by_this_probe(self):
        assert load("netflix.yaml").is_measurable is False

    def test_gate_returns_unmeasurable_rather_than_a_verdict(self):
        v = evaluate(load("netflix.yaml"), _report(lufs=-27.0))
        assert v.status == UNMEASURABLE
        assert v.status not in (PASS, BLOCKED)

    def test_it_refuses_even_when_the_number_would_have_passed(self):
        """-27.0 matches the target exactly, and it still must not adjudicate."""
        v = evaluate(load("netflix.yaml"), _report(lufs=-27.0))
        assert v.status == UNMEASURABLE

    def test_the_refusal_names_what_is_missing(self):
        v = evaluate(load("netflix.yaml"), _report())
        message = v.checks[0].message
        assert "bs1770_dialog_gated" in message
        assert "bs1770_gated" in message

    def test_measurable_profiles_are_unaffected(self):
        assert evaluate(load("ebu_r128.yaml"), _report()).status == PASS
