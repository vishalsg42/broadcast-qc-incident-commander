"""Evidence ledger and citation validator tests.

The tests a judge would look for are `TestCitationValidator` - specifically that an
uncited conclusion is REJECTED, and that a fabricated citation cannot pass. A
validator with no test proving it fires is indistinguishable from a costume.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent.evidence import (
    Claim,
    ClaimType,
    Conclusion,
    EvidenceLedger,
    LedgerError,
    Phase,
    ProposedAction,
    allowlist_from_profile,
    safe_record_interpretation,
    validate_conclusion,
)

PROFILE = Path(__file__).parent.parent / "pipeline" / "profiles" / "ebu_r128.yaml"


@pytest.fixture
def allowlist() -> dict:
    with PROFILE.open() as fh:
        return allowlist_from_profile(yaml.safe_load(fh))


@pytest.fixture
def ledger() -> EvidenceLedger:
    """A ledger with one real observation per phase."""
    led = EvidenceLedger(run_id="run-test")
    for phase, query, raw in [
        (Phase.BASELINE, '{job="ingest"} |= "asset-001"', {"integrated_lufs": -23.1}),
        (Phase.DIVERGENCE, '{job="qc"} |= "asset-001"', {"first_bad_stage": "package"}),
        (Phase.ACTOR, '{resource.service.name="package"}', {"preset": "pkg_h264_v7"}),
        (Phase.CAUSE, '{job="package"} |= "pkg_h264_v7"', {"remap": "wrong"}),
    ]:
        led.observe(phase, query, raw)
        led.record_interpretation(finding=f"{phase.value} finding", supports=True)
    return led


class TestProvenanceIsControllerBound:
    """The model must not be able to name the query it claims to have run."""

    def test_query_is_recorded_by_the_controller(self, ledger):
        step = ledger.steps_for(Phase.BASELINE)[0]
        assert step.query_used == '{job="ingest"} |= "asset-001"'
        assert step.query_hash and len(step.query_hash) == 16

    def test_model_cannot_interpret_without_an_observation(self):
        led = EvidenceLedger()
        with pytest.raises(LedgerError):
            led.record_interpretation(finding="I looked it up", supports=True)

    def test_safe_wrapper_returns_error_instead_of_raising(self):
        """ADK aborts the invocation on an exception, so tools must return data."""
        led = EvidenceLedger()
        out = safe_record_interpretation(led, finding="fabricated", supports=True)
        assert out["ok"] is False
        assert "no pending observation" in out["error"]

    def test_safe_wrapper_rejects_empty_finding(self):
        led = EvidenceLedger()
        led.observe(Phase.BASELINE, "q", {})
        out = safe_record_interpretation(led, finding="", supports=True)
        assert out["ok"] is False
        assert "finding" in out["error"]

    def test_raw_result_is_retained_not_summarised_away(self, ledger):
        step = ledger.steps_for(Phase.ACTOR)[0]
        assert ledger.raw(step.raw_result_ref) == {"preset": "pkg_h264_v7"}

    def test_one_observation_consumed_per_interpretation(self):
        led = EvidenceLedger()
        led.observe(Phase.BASELINE, "q", {})
        assert safe_record_interpretation(led, finding="a", supports=True)["ok"] is True
        assert safe_record_interpretation(led, finding="b", supports=True)["ok"] is False


class TestPhaseCompletion:
    def test_controller_decides_completion(self, ledger):
        assert all(ledger.phase_complete(p) for p in Phase)

    def test_incomplete_phase_is_detected(self):
        led = EvidenceLedger()
        led.observe(Phase.BASELINE, "q", {})
        led.record_interpretation(finding="f", supports=True)
        assert led.phase_complete(Phase.BASELINE)
        assert not led.phase_complete(Phase.CAUSE)


class TestCitationValidator:
    def test_well_cited_conclusion_is_accepted(self, ledger, allowlist):
        c = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    claim_value="preset pkg_h264_v7 remapped channels at package",
                    supporting_step_ids=["step-03", "step-04"],
                    confidence="high",
                )
            ]
        )
        assert validate_conclusion(c, ledger, allowlist=allowlist).ok

    def test_uncited_conclusion_is_rejected(self, ledger):
        """THE test. A claim with no supporting evidence must never pass."""
        with pytest.raises(ValidationError):
            Claim(
                claim_type=ClaimType.ROOT_CAUSE,
                claim_value="it was the encoder, trust me",
                supporting_step_ids=[],
                confidence="high",
            )

    def test_fabricated_citation_is_rejected(self, ledger):
        c = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    claim_value="plausible but unsourced",
                    supporting_step_ids=["step-99"],
                    confidence="high",
                )
            ]
        )
        res = validate_conclusion(c, ledger)
        assert not res.ok
        assert "unknown step" in res.errors[0]

    def test_partially_fabricated_citation_is_rejected(self, ledger):
        """One real citation does not launder a fake one."""
        c = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    claim_value="half true",
                    supporting_step_ids=["step-01", "step-42"],
                    confidence="medium",
                )
            ]
        )
        res = validate_conclusion(c, ledger)
        assert not res.ok
        assert any("step-42" in e for e in res.errors)

    def test_errors_name_the_known_steps_so_the_model_can_correct(self, ledger):
        c = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.NO_FAULT,
                    claim_value="x",
                    supporting_step_ids=["nope"],
                    confidence="low",
                )
            ]
        )
        res = validate_conclusion(c, ledger)
        assert "step-01" in res.errors[0]

    def test_conclusion_requires_at_least_one_claim(self):
        with pytest.raises(ValidationError):
            Conclusion(claims=[])


class TestActionAllowlist:
    def test_allowlist_is_read_from_the_profile(self, allowlist):
        assert set(allowlist) == {
            "reencode_with_profile",
            "reencode_with_loudness_target",
            "escalate_to_human",
        }

    def _concl(self, action: ProposedAction) -> Conclusion:
        return Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.ROOT_CAUSE,
                    claim_value="v",
                    supporting_step_ids=["step-01"],
                    confidence="high",
                )
            ],
            proposed_action=action,
        )

    def test_permitted_action_accepted(self, ledger, allowlist):
        a = ProposedAction(
            action_id="reencode_with_profile",
            params={"profile_id": "ebu-r128-tv"},
            rationale="re-run packaging with the correct profile",
        )
        assert validate_conclusion(self._concl(a), ledger, allowlist=allowlist).ok

    def test_action_not_on_allowlist_rejected(self, ledger, allowlist):
        """The adversarial case shown in the demo."""
        a = ProposedAction(
            action_id="run_shell_command",
            params={"cmd": "ffmpeg -i in.mp4 -af volume=5dB out.mp4"},
            rationale="just fix it directly",
        )
        res = validate_conclusion(self._concl(a), ledger, allowlist=allowlist)
        assert not res.ok
        assert "not on the allowlist" in res.errors[0]

    def test_out_of_range_parameter_rejected(self, ledger, allowlist):
        a = ProposedAction(
            action_id="reencode_with_loudness_target",
            params={"target_lufs": -60.0},  # allowed range is -31..-16
            rationale="crank it",
        )
        res = validate_conclusion(self._concl(a), ledger, allowlist=allowlist)
        assert not res.ok
        assert "below min" in res.errors[0]

    def test_enum_violation_rejected(self, ledger, allowlist):
        a = ProposedAction(
            action_id="reencode_with_profile",
            params={"profile_id": "netflix-dialogue-gated"},
            rationale="use a profile we do not ship",
        )
        res = validate_conclusion(self._concl(a), ledger, allowlist=allowlist)
        assert not res.ok
        assert "not in" in res.errors[0]

    def test_missing_parameter_rejected(self, ledger, allowlist):
        a = ProposedAction(action_id="reencode_with_profile", params={}, rationale="r")
        res = validate_conclusion(self._concl(a), ledger, allowlist=allowlist)
        assert not res.ok
        assert "missing parameter" in res.errors[0]

    def test_smuggled_extra_parameter_rejected(self, ledger, allowlist):
        a = ProposedAction(
            action_id="reencode_with_profile",
            params={"profile_id": "ebu-r128-tv", "extra_args": "-af volume=10dB"},
            rationale="sneak an argument through",
        )
        res = validate_conclusion(self._concl(a), ledger, allowlist=allowlist)
        assert not res.ok
        assert "unexpected parameter" in res.errors[0]
