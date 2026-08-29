"""Each scenario has a different correct answer.

A system that reaches the same conclusion whatever it is shown has not concluded
anything. Two of these scenarios present the IDENTICAL symptom - delivered audio
out of spec - and are not the same problem:

  fault         a preset changed hours ago and broke the delivery
  wrong-preset  a valid preset, unchanged since June, applied to content it does
                not suit

The distinction is the difference between "roll back the change" and "talk to
whoever selected this profile", and getting it wrong sends someone to the wrong
person. A third, black-fault, is not a loudness problem at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.policy import BLOCKED, evaluate, load_profile
from pipeline.stages import NORMALIZE, PACKAGE, PresetLibrary, run_pipeline
from server.orchestrator import FIXTURES

MEDIA = Path(__file__).parent.parent / "media"


class TestScenarioDefinitions:
    def test_every_scenario_names_media_that_exists(self):
        for name, (media, _) in FIXTURES.items():
            assert (MEDIA / media).is_file(), f"{name} needs {media}"

    def test_every_override_names_a_real_preset(self):
        library = PresetLibrary.load()
        for name, (_, overrides) in FIXTURES.items():
            for stage, preset_id in overrides.items():
                assert library.get(stage, preset_id), f"{name}: {preset_id}"

    def test_the_two_loudness_scenarios_break_at_different_stages(self):
        """Same symptom, different stage, different preset - by construction."""
        assert FIXTURES["fault"][1] == {PACKAGE: "pkg_h264_v7"}
        assert FIXTURES["wrong-preset"][1] == {NORMALIZE: "norm_ebu_v2"}

    def test_the_mis_selected_preset_is_genuinely_old(self):
        """`wrong-preset` only means anything if nothing recently changed.

        If this preset were recently modified the scenario collapses into
        `fault`, and the SELECTION-versus-change distinction it exists to
        demonstrate disappears.
        """
        from agent.investigator import _changed_recently

        preset = PresetLibrary.load().get(NORMALIZE, "norm_ebu_v2")
        assert _changed_recently(preset.changed_at) is False

    def test_the_injected_preset_is_never_the_stage_default(self):
        """An override that matches the default injects nothing."""
        library = PresetLibrary.load()
        for name, (_, overrides) in FIXTURES.items():
            for stage, preset_id in overrides.items():
                assert library.default_for(stage).id != preset_id, name


@pytest.mark.media
class TestScenariosProduceDifferentFaults:
    @pytest.fixture(scope="class")
    def profile(self):
        return load_profile("ebu-r128-tv")

    def _run(self, name, profile):
        media, overrides = FIXTURES[name]
        return run_pipeline(
            MEDIA / media,
            out_dir="out",
            overrides=overrides or None,
            black_opts=profile.black_detector_opts,
            profile=profile,
        )

    def test_black_fault_blocks_on_black_with_loudness_in_spec(self, profile):
        """The one that must not be blamed on a preset.

        Every loudness check passes. An investigation that reaches for the
        nearest transcode preset here has reached for the wrong thing.
        """
        pr = self._run("black-fault", profile)
        verdict = evaluate(profile, pr.stages[-1].qc)
        assert verdict.status == BLOCKED

        by_id = {c.check_id: c for c in verdict.checks}
        assert by_id["black.body"].status == BLOCKED
        assert by_id["loudness.integrated"].status == "PASS"
        assert by_id["loudness.true_peak"].status == "PASS"

    def test_wrong_preset_breaks_earlier_than_the_preset_change(self, profile):
        """Same symptom as `fault`, one stage earlier, from an unchanged preset."""
        pr = self._run("wrong-preset", profile)
        assert evaluate(profile, pr.stages[-1].qc).status == BLOCKED

        normalize = pr.stage(NORMALIZE)
        assert normalize.preset.id == "norm_ebu_v2"
        # It normalises to the old house target, so it is already wrong here -
        # unlike `fault`, where normalize does its job and package breaks it.
        assert evaluate(profile, normalize.qc).status == BLOCKED
        assert pr.stage(PACKAGE).preset.id == "pkg_h264_v6"

    def test_the_preset_fault_leaves_normalize_correct(self, profile):
        """The contrast: in `fault` the defect appears only at package."""
        pr = self._run("fault", profile)
        assert evaluate(profile, pr.stage(NORMALIZE).qc).status == "PASS"
        assert evaluate(profile, pr.stage(PACKAGE).qc).status == BLOCKED
