"""The rules the agent cannot talk its way past.

The agent chooses its own tools, so the guarantees can no longer come from the
controller running a fixed sequence. They come from what `conclude` refuses.

These are the refusals, tested directly. Each one fired against the real model
during development, and each time the model corrected itself and went on to a
valid conclusion - which is the behaviour worth keeping.
"""

from __future__ import annotations

import json

import pytest

from agent.autonomous import (
    CLAIM_SOURCES,
    MIN_SOURCES_BEFORE_CONCLUDING,
    AutonomousInvestigator,
    check_claim_sources,
)
from agent.evidence import Claim, ClaimType, Conclusion, EvidenceLedger, Phase
from pipeline.policy import load_profile


@pytest.fixture
def profile():
    return load_profile("ebu-r128-tv")


@pytest.fixture
def allowlist():
    return {
        "reencode_with_profile": {"profile_id": {"enum": ["ebu-r128-tv"]}},
        "escalate_to_human": {"reason": {"type": "string", "max_length": 500}},
    }


def _ledger_with(*phases: Phase) -> EvidenceLedger:
    led = EvidenceLedger(run_id="agent-test")
    for i, phase in enumerate(phases):
        pending = led.observe(phase, f"query {i}", {"n": i})
        led.record_interpretation("observed", True, step_id=pending.step_id)
    return led


def _agent(ledger, profile, allowlist) -> AutonomousInvestigator:
    return AutonomousInvestigator(
        ledger=ledger, pipeline_run=None, profile=profile, allowlist=allowlist
    )


def _claims(claim_type: str, step_ids: list[str]) -> str:
    return json.dumps(
        [
            {
                "claim_type": claim_type,
                "claim_value": "a claim about the failure",
                "supporting_step_ids": step_ids,
                "confidence": "high",
            }
        ]
    )


class TestEvidenceFloor:
    def test_concluding_without_investigating_is_refused(self, profile, allowlist):
        """One tool call and an assertion is not an investigation."""
        led = _ledger_with(Phase.DIVERGENCE)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(_claims("DIVERGENCE_STAGE", ["step-01"]), "", "")
        assert out["ok"] is False
        assert "investigate further" in out["error"]

    def test_the_floor_is_kinds_of_evidence_not_number_of_calls(self, profile, allowlist):
        """Ten queries of the same kind is still one kind of evidence."""
        led = _ledger_with(*([Phase.DIVERGENCE] * 10))
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(_claims("DIVERGENCE_STAGE", ["step-01"]), "", "")
        assert out["ok"] is False
        assert str(MIN_SOURCES_BEFORE_CONCLUDING) in out["error"]


class TestUntestedAttribution:
    def test_blaming_a_preset_without_testing_it_is_refused(self, profile, allowlist):
        """A preset that RAN is not a preset that CAUSED."""
        led = _ledger_with(Phase.BASELINE, Phase.DIVERGENCE, Phase.ACTOR)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(_claims("ROOT_CAUSE", ["step-03"]), "", "")
        assert out["ok"] is False
        assert "no experiment was run" in out["error"]

    def test_the_same_conclusion_is_allowed_once_it_has_been_tested(self, profile, allowlist):
        led = _ledger_with(Phase.BASELINE, Phase.DIVERGENCE, Phase.ACTOR, Phase.EXPERIMENT)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(_claims("ROOT_CAUSE", ["step-04"]), "", "")
        assert out["ok"] is True, out.get("error")

    def test_a_claim_that_blames_nothing_needs_no_experiment(self, profile, allowlist):
        led = _ledger_with(Phase.BASELINE, Phase.DIVERGENCE, Phase.ACTOR)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(_claims("SOURCE_IN_SPEC", ["step-01"]), "", "")
        assert out["ok"] is True, out.get("error")


class TestClaimSources:
    def test_a_measured_claim_must_cite_the_measurement(self):
        """The rule that matters: no experiment, no experimental claim.

        Without it, "I tested it and measured +6.1 LU" citing a preset lookup
        passes every structural check with no experiment ever run.
        """
        led = _ledger_with(Phase.BASELINE, Phase.CAUSE)
        conclusion = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.EXPERIMENT,
                    claim_value="measured a rise of 6.1 LU",
                    supporting_step_ids=["step-02"],
                    confidence="high",
                )
            ]
        )
        errors = check_claim_sources(conclusion, led)
        assert errors and "EXPERIMENT" in errors[0]

    def test_the_same_claim_citing_the_experiment_passes(self):
        led = _ledger_with(Phase.BASELINE, Phase.EXPERIMENT)
        conclusion = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.EXPERIMENT,
                    claim_value="measured a rise of 6.1 LU",
                    supporting_step_ids=["step-02"],
                    confidence="high",
                )
            ]
        )
        assert check_claim_sources(conclusion, led) == []

    def test_attribution_may_rest_on_the_experiment(self):
        """The experiment ran that exact preset, so it bears on which preset acted."""
        assert Phase.EXPERIMENT in CLAIM_SOURCES[ClaimType.ACTOR_PRESET]

    def test_an_uncited_phase_is_not_invented(self):
        """Citing a step that does not exist is the validator's job, not this one."""
        led = _ledger_with(Phase.EXPERIMENT)
        conclusion = Conclusion(
            claims=[
                Claim(
                    claim_type=ClaimType.EXPERIMENT,
                    claim_value="x",
                    supporting_step_ids=["step-99"],
                    confidence="high",
                )
            ]
        )
        assert check_claim_sources(conclusion, led) == []


class TestMalformedInput:
    def test_bad_json_is_reported_not_raised(self, profile, allowlist):
        """A tool that raises aborts the whole ADK invocation."""
        led = _ledger_with(Phase.BASELINE, Phase.DIVERGENCE, Phase.EXPERIMENT)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude("{not json", "", "")
        assert out["ok"] is False
        assert "not valid" in out["error"]

    def test_an_off_allowlist_action_is_refused(self, profile, allowlist):
        led = _ledger_with(Phase.BASELINE, Phase.DIVERGENCE, Phase.EXPERIMENT)
        agent = _agent(led, profile, allowlist)
        out = agent._tool_conclude(
            _claims("SOURCE_IN_SPEC", ["step-01"]), "run_shell_command", "just fix it"
        )
        assert out["ok"] is False
