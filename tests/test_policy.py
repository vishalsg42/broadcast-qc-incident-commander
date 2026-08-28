"""Policy engine tests.

The black-policy cases are the ones that matter most: a naive implementation that
treats any black frame as a defect would reject nearly every legitimate master,
because deliverables *mandate* head black, bars and tone, slate, and break black.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.policy import BLOCKED, PASS, Profile, evaluate
from pipeline.qc import BlackInterval, LoudnessMeasurement, QCReport

PROFILE = Path(__file__).parent.parent / "pipeline" / "profiles" / "ebu_r128.yaml"


@pytest.fixture
def profile() -> Profile:
    return Profile.load(PROFILE)


def _report(
    *,
    lufs: float = -23.0,
    true_peak: float = -2.0,
    black: list[BlackInterval] | None = None,
    duration: float = 45.0,
) -> QCReport:
    if black is None:
        # Legal by default: 10s head, ~2s tail.
        black = [
            BlackInterval(0.0, 10.0, 10.0),
            BlackInterval(43.0, 44.96, 1.96),
        ]
    return QCReport(
        asset_path="synthetic",
        duration_s=duration,
        loudness=LoudnessMeasurement(lufs, true_peak, -33.0, 1.3),
        black_intervals=black,
    )


def _check(verdict, check_id):
    return next(c for c in verdict.checks if c.check_id == check_id)


class TestLoudness:
    def test_on_target_passes(self, profile):
        assert evaluate(profile, _report(lufs=-23.0)).status == PASS

    @pytest.mark.parametrize("lufs", [-23.5, -22.5])
    def test_within_tolerance_passes(self, profile, lufs):
        assert evaluate(profile, _report(lufs=lufs)).status == PASS

    @pytest.mark.parametrize("lufs", [-23.6, -22.4, -18.1, -30.0])
    def test_outside_tolerance_blocks(self, profile, lufs):
        v = evaluate(profile, _report(lufs=lufs))
        assert v.status == BLOCKED
        assert _check(v, "loudness.integrated").failed

    def test_true_peak_over_ceiling_blocks(self, profile):
        v = evaluate(profile, _report(true_peak=-0.2))
        assert _check(v, "loudness.true_peak").failed


class TestBlackPolicy:
    """Black is a policy, not a boolean."""

    def test_mandated_head_and_tail_black_is_legal(self, profile):
        v = evaluate(profile, _report())
        assert v.status == PASS
        assert not _check(v, "black.body").failed

    def test_missing_required_head_black_blocks(self, profile):
        v = evaluate(profile, _report(black=[BlackInterval(43.0, 44.96, 1.96)]))
        assert v.status == BLOCKED
        assert _check(v, "black.required.head_black").failed

    def test_illegal_black_inside_body_blocks(self, profile):
        v = evaluate(
            profile,
            _report(
                black=[
                    BlackInterval(0.0, 10.0, 10.0),
                    BlackInterval(20.0, 22.04, 2.04),
                    BlackInterval(43.0, 44.96, 1.96),
                ]
            ),
        )
        assert v.status == BLOCKED
        body = _check(v, "black.body")
        assert body.failed
        assert "20.00-22.04s" in body.message

    def test_short_body_black_within_tolerance_passes(self, profile):
        """0.8s is under the 1.0s max_contiguous_black_s - a cut, not a defect."""
        v = evaluate(
            profile,
            _report(
                black=[
                    BlackInterval(0.0, 10.0, 10.0),
                    BlackInterval(25.0, 25.8, 0.8),
                    BlackInterval(43.0, 44.96, 1.96),
                ]
            ),
        )
        assert not _check(v, "black.body").failed

    def test_black_straddling_head_boundary_counts_only_the_overhang(self, profile):
        """Black 0-11.5s: 10s is legal head, the 1.5s overhang is a body defect."""
        v = evaluate(
            profile,
            _report(black=[BlackInterval(0.0, 11.5, 11.5), BlackInterval(43.0, 44.96, 1.96)]),
        )
        body = _check(v, "black.body")
        assert body.failed
        assert "10.00-11.50s" in body.message

    def test_tail_region_resolves_relative_to_duration(self, profile):
        """Tail region is expressed as -2.0..0; it must follow the asset length."""
        v = evaluate(
            profile,
            _report(
                duration=60.0,
                black=[BlackInterval(0.0, 10.0, 10.0), BlackInterval(58.0, 60.0, 2.0)],
            ),
        )
        assert v.status == PASS


class TestVerdictShape:
    def test_blocked_when_any_check_fails(self, profile):
        v = evaluate(profile, _report(lufs=-18.0))
        assert v.status == BLOCKED
        assert len(v.failures) >= 1

    def test_verdict_is_machine_readable(self, profile):
        d = evaluate(profile, _report()).to_dict()
        assert d["profile_id"] == "ebu-r128-tv"
        assert all({"check_id", "status", "measured", "expected"} <= c.keys() for c in d["checks"])

    def test_same_input_same_verdict(self, profile):
        """Determinism: the gate is a pure function, called twice on block and clear."""
        a = evaluate(profile, _report(lufs=-18.1)).to_dict()
        b = evaluate(profile, _report(lufs=-18.1)).to_dict()
        assert a == b
