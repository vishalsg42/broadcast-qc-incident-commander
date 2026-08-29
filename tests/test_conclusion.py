"""Conclusion assembly and the refusal path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.conclusion import (
    adversarial_candidates,
    build_conclusion,
    render_prose,
    screen_candidate,
)
from agent.evidence import (
    PHASE_ORDER,
    ClaimType,
    EvidenceLedger,
    allowlist_from_profile,
    validate_conclusion,
)

PROFILE = Path(__file__).parent.parent / "pipeline" / "profiles" / "ebu_r128.yaml"


@pytest.fixture
def allowlist() -> dict:
    with PROFILE.open() as fh:
        return allowlist_from_profile(yaml.safe_load(fh))


@pytest.fixture
def ledger() -> EvidenceLedger:
    led = EvidenceLedger(run_id="run-test")
    for phase in PHASE_ORDER:
        led.observe(phase, f"query for {phase.value}", {"data": phase.value})
        led.record_interpretation(finding=f"{phase.value} finding", supports=True)
    return led


def _fault_args() -> dict:
    return {
        "source_in_spec": True,
        "failing_stage": "package",
        "preset_id": "pkg_h264_v7",
        "preset_version": 7,
        "preset_changed_at": "2026-08-29T14:02:00Z",
        "cause_detail": "pan=stereo|c0=c0+c1|c1=c0+c1 sums both channels into each output",
        "delivery_profile_id": "ebu-r128-tv",
    }


class TestHappyPath:
    def test_conclusion_validates(self, ledger, allowlist):
        c = build_conclusion(ledger, **_fault_args())
        assert validate_conclusion(c, ledger, allowlist=allowlist).ok

    def test_every_claim_cites_real_steps(self, ledger, allowlist):
        c = build_conclusion(ledger, **_fault_args())
        known = ledger.step_ids()
        for claim in c.claims:
            assert claim.supporting_step_ids
            assert set(claim.supporting_step_ids) <= known

    def test_attributes_the_preset_version(self, ledger):
        c = build_conclusion(ledger, **_fault_args())
        actor = next(x for x in c.claims if x.claim_type == ClaimType.ACTOR_PRESET)
        assert "pkg_h264_v7" in actor.claim_value
        assert "v7" in actor.claim_value


class TestSelectionVersusChange:
    """WHICH preset ran and WHETHER it changed are independent facts.

    Fusing them tells one causal story - "a preset changed broke this" - and the
    more common real fault is a preset that changed nothing and was MIS-SELECTED:
    a stereo title routed through the 5.1 fold-down profile. Same symptom, same
    filter, changed_at from months ago. Reporting them separately keeps the
    conclusion true in both cases.
    """

    def test_recent_change_is_offered_as_a_plausible_trigger(self, ledger):
        c = build_conclusion(ledger, **(_fault_args() | {"recently_changed": True}))
        claims = [x.claim_value for x in c.claims if x.claim_type == ClaimType.ACTOR_PRESET]
        assert any("changed recently" in v for v in claims)
        assert any("plausible trigger" in v for v in claims)

    def test_an_old_preset_is_reported_as_SELECTION_not_a_change(self, ledger):
        c = build_conclusion(ledger, **(_fault_args() | {"recently_changed": False}))
        claims = [x.claim_value for x in c.claims if x.claim_type == ClaimType.ACTOR_PRESET]
        assert any("SELECTION rather than a preset change" in v for v in claims)
        assert not any("plausible trigger" in v for v in claims)

    def test_unknown_change_state_asserts_neither(self, ledger):
        """None means we could not determine it. Say nothing rather than guess."""
        c = build_conclusion(ledger, **(_fault_args() | {"recently_changed": None}))
        claims = [x.claim_value for x in c.claims if x.claim_type == ClaimType.ACTOR_PRESET]
        assert len(claims) == 1
        assert "changed recently" not in claims[0]
        assert "SELECTION" not in claims[0]

    def test_the_change_claim_is_hedged_lower_than_the_observation(self, ledger):
        """Which preset ran is observed. Whether the change caused it is inferred."""
        c = build_conclusion(ledger, **(_fault_args() | {"recently_changed": True}))
        actor = [x for x in c.claims if x.claim_type == ClaimType.ACTOR_PRESET]
        assert actor[0].confidence == "high"
        assert actor[1].confidence == "medium"

    def test_root_cause_is_hedged_not_asserted(self, ledger):
        """A preset changing before a failure is correlation, not proof."""
        c = build_conclusion(ledger, **_fault_args())
        root = next(x for x in c.claims if x.claim_type == ClaimType.ROOT_CAUSE)
        assert "Most likely" in root.claim_value

    def test_proposes_an_allowlisted_repair(self, ledger, allowlist):
        c = build_conclusion(ledger, **_fault_args())
        assert c.proposed_action.action_id in allowlist

    def test_prose_is_derived_from_claims(self, ledger):
        c = build_conclusion(ledger, **_fault_args())
        text = render_prose(c)
        for claim in c.claims:
            assert claim.claim_value in text


class TestNegativeCases:
    """The two happy-path breakers - an agent that only ever finds a fault is a puppet."""

    def test_source_out_of_spec_proposes_no_repair(self, ledger, allowlist):
        args = _fault_args() | {"source_in_spec": False}
        c = build_conclusion(ledger, **args)
        assert any(x.claim_type == ClaimType.SOURCE_OUT_OF_SPEC for x in c.claims)
        assert c.proposed_action.action_id == "escalate_to_human"
        assert validate_conclusion(c, ledger, allowlist=allowlist).ok

    def test_source_out_of_spec_does_not_blame_a_stage(self, ledger):
        c = build_conclusion(ledger, **(_fault_args() | {"source_in_spec": False}))
        assert not any(x.claim_type == ClaimType.ROOT_CAUSE for x in c.claims)

    def test_no_fault_takes_no_action(self, ledger, allowlist):
        args = _fault_args() | {"failing_stage": None}
        c = build_conclusion(ledger, **args)
        assert any(x.claim_type == ClaimType.NO_FAULT for x in c.claims)
        assert c.proposed_action is None
        assert validate_conclusion(c, ledger, allowlist=allowlist).ok


class TestRefusal:
    """Every candidate goes through the SAME validator as the real path."""

    def test_all_adversarial_candidates_are_refused(self, ledger, allowlist):
        candidates = adversarial_candidates(ledger)
        assert len(candidates) == 4
        for cand in candidates:
            result = screen_candidate(cand, ledger, allowlist)
            assert not result.ok, f"{cand.name} was NOT refused"
            assert result.errors

    def test_refusal_is_deterministic(self, ledger, allowlist):
        """The on-camera refusal cannot depend on the model misbehaving."""
        first = [
            screen_candidate(c, ledger, allowlist).errors
            for c in adversarial_candidates(ledger)
        ]
        second = [
            screen_candidate(c, ledger, allowlist).errors
            for c in adversarial_candidates(ledger)
        ]
        assert first == second

    @pytest.mark.parametrize(
        "name,fragment",
        [
            ("fabricated_citation", "unknown step"),
            ("laundered_citation", "step-42"),
            ("off_allowlist_action", "not on the allowlist"),
            ("out_of_range_parameter", "below min"),
        ],
    )
    def test_each_candidate_fails_for_the_right_reason(
        self, ledger, allowlist, name, fragment
    ):
        cand = next(c for c in adversarial_candidates(ledger) if c.name == name)
        result = screen_candidate(cand, ledger, allowlist)
        assert any(fragment in e for e in result.errors), result.errors
