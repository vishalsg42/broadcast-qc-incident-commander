"""Repair execution, re-validation, and the full deterministic loop.

`TestEndToEndLoop` is the acceptance test for everything that does not involve a
model: block -> repair from the allowlist -> re-validate with the SAME gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.evidence import allowlist_from_profile
from pipeline.policy import BLOCKED, PASS, Profile, evaluate
from pipeline.remediation import RemediationError, execute_repair
from pipeline.stages import NORMALIZE, PACKAGE, run_pipeline

MEDIA = Path(__file__).parent.parent / "media"
PROFILE = Path(__file__).parent.parent / "pipeline" / "profiles" / "ebu_r128.yaml"

pytestmark = pytest.mark.skipif(
    not (MEDIA / "master_good.mp4").exists(),
    reason="fixtures missing - run scripts/make_fixtures.sh",
)


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load(PROFILE)


@pytest.fixture(scope="module")
def allowlist(profile) -> dict:
    return allowlist_from_profile(profile.raw)


@pytest.fixture(scope="module")
def faulted(profile, tmp_path_factory):
    """A delivered asset blocked by the pkg_h264_v7 channel-remap fault."""
    return run_pipeline(
        MEDIA / "master_good.mp4",
        out_dir=tmp_path_factory.mktemp("e2e"),
        overrides={PACKAGE: "pkg_h264_v7"},
        black_opts=profile.black_detector_opts,
    )


class TestExecutorRevalidatesTheRequest:
    """The executor never trusts a request just because the agent proposed it."""

    def test_off_allowlist_action_refused(self, profile, allowlist, tmp_path):
        with pytest.raises(RemediationError, match="not on the allowlist"):
            execute_repair(
                "run_shell_command",
                {"cmd": "rm -rf /"},
                source_path=MEDIA / "master_good.mp4",
                profile=profile,
                out_dir=tmp_path,
                allowlist=allowlist,
            )

    def test_out_of_range_parameter_refused(self, profile, allowlist, tmp_path):
        with pytest.raises(RemediationError, match="below min"):
            execute_repair(
                "reencode_with_loudness_target",
                {"target_lufs": -99.0},
                source_path=MEDIA / "master_good.mp4",
                profile=profile,
                out_dir=tmp_path,
                allowlist=allowlist,
            )

    def test_smuggled_parameter_refused(self, profile, allowlist, tmp_path):
        with pytest.raises(RemediationError, match="unexpected parameter"):
            execute_repair(
                "reencode_with_profile",
                {"profile_id": "ebu-r128-tv", "extra_args": "-af volume=20dB"},
                source_path=MEDIA / "master_good.mp4",
                profile=profile,
                out_dir=tmp_path,
                allowlist=allowlist,
            )

    def test_escalation_executes_nothing(self, profile, allowlist, tmp_path):
        res = execute_repair(
            "escalate_to_human",
            {"reason": "source arrived out of spec; not a pipeline fault"},
            source_path=MEDIA / "master_good.mp4",
            profile=profile,
            out_dir=tmp_path,
            allowlist=allowlist,
        )
        assert res.executed is False
        assert res.output_path is None
        assert not res.resolved


class TestEndToEndLoop:
    """block -> repair -> re-validate, with no model in the loop."""

    def test_delivered_asset_is_blocked(self, profile, faulted):
        assert evaluate(profile, faulted.stage(PACKAGE).qc).status == BLOCKED

    def test_repair_resolves_and_writes_a_new_artefact(
        self, profile, allowlist, faulted, tmp_path
    ):
        # Repair the last IN-SPEC artefact, not the damaged delivery: re-encoding
        # the broken file would bake the defect in.
        good_input = faulted.stage(NORMALIZE).output_path

        res = execute_repair(
            "reencode_with_profile",
            {"profile_id": "ebu-r128-tv"},
            source_path=good_input,
            profile=profile,
            out_dir=tmp_path,
            allowlist=allowlist,
        )

        assert res.executed
        assert res.resolved, res.verdict.to_dict() if res.verdict else res.message
        assert res.verdict.status == PASS
        # Never overwrites its input.
        assert res.output_path != good_input
        assert Path(res.output_path).exists()

    def test_revalidation_uses_the_same_gate_that_blocked(
        self, profile, allowlist, faulted, tmp_path
    ):
        """The code that says 'no' is the code that says 'yes'."""
        before = evaluate(profile, faulted.stage(PACKAGE).qc)
        res = execute_repair(
            "reencode_with_profile",
            {"profile_id": "ebu-r128-tv"},
            source_path=faulted.stage(NORMALIZE).output_path,
            profile=profile,
            out_dir=tmp_path,
            allowlist=allowlist,
        )
        assert before.status == BLOCKED
        assert res.verdict.status == PASS
        assert res.verdict.profile_id == before.profile_id
        assert res.verdict.profile_version == before.profile_version

    def test_repairs_never_overwrite_each_other(self, profile, allowlist, faulted, tmp_path):
        """Each repair is a new generation, so evidence cannot be destroyed."""
        src = faulted.stage(NORMALIZE).output_path
        a = execute_repair(
            "reencode_with_profile",
            {"profile_id": "ebu-r128-tv"},
            source_path=src,
            profile=profile,
            out_dir=tmp_path,
            allowlist=allowlist,
        )
        b = execute_repair(
            "reencode_with_profile",
            {"profile_id": "ebu-r128-tv"},
            source_path=src,
            profile=profile,
            out_dir=tmp_path,
            allowlist=allowlist,
        )
        assert a.output_path != b.output_path
        assert Path(a.output_path).exists() and Path(b.output_path).exists()
