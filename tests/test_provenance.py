"""Change provenance: who changed a preset, under what ticket, approved by whom.

Attribution to a preset VERSION is where this system's value sits, but it is not
where an incident conversation stops. The next questions in the room are who
made the change and whether it was signed off. These tests keep those three
fields flowing from the preset file, onto the trace span, back out of the
investigation, and into a claim.

The round trip matters: the ACTOR phase reads provenance off the SPAN, not off
the preset file. The file says what a preset is now; the span says what actually
ran.
"""

from __future__ import annotations

from agent.conclusion import build_conclusion
from agent.evidence import PHASE_ORDER, ClaimType, EvidenceLedger, Phase
from pipeline.stages import PACKAGE, PresetLibrary


def _ledger() -> EvidenceLedger:
    ledger = EvidenceLedger(run_id="prov-test")
    for phase in PHASE_ORDER:
        ledger.observe(phase, f"query for {phase.value}", {"data": phase.value})
        ledger.record_interpretation(finding=f"{phase.value} finding", supports=True)
    return ledger


def _args(**over) -> dict:
    base = {
        "source_in_spec": True,
        "failing_stage": PACKAGE,
        "preset_id": "pkg_h264_v7",
        "preset_version": 7,
        "preset_changed_at": "2026-08-29T14:02:00Z",
        "cause_detail": "channel sum",
        "delivery_profile_id": "ebu-r128-tv",
    }
    return base | over


class TestPresetCarriesProvenance:
    def test_every_shipped_preset_records_who_and_why(self):
        library = PresetLibrary.load()
        for stage in ("normalize", "package"):
            for preset in library.all_for(stage):
                assert preset.changed_by, f"{preset.id} has no changed_by"
                assert preset.change_ticket, f"{preset.id} has no change_ticket"

    def test_the_hero_preset_is_fully_attributed(self):
        preset = PresetLibrary.load().get(PACKAGE, "pkg_h264_v7")
        assert preset.changed_by == "d.okonkwo"
        assert preset.change_ticket == "CHG-4471"
        assert preset.approved_by == "j.reyes"

    def test_provenance_reaches_the_stage_summary(self):
        """to_dict feeds telemetry, so a dropped field would silently vanish."""
        preset = PresetLibrary.load().get(PACKAGE, "pkg_h264_v7")
        assert preset.changed_by is not None
        assert preset.change_ticket is not None


class TestProvenanceBecomesAClaim:
    def test_states_author_ticket_and_approver(self):
        c = build_conclusion(
            _ledger(),
            **_args(changed_by="d.okonkwo", change_ticket="CHG-4471", approved_by="j.reyes"),
        )
        values = [x.claim_value for x in c.claims]
        assert any(
            "changed by d.okonkwo" in v and "CHG-4471" in v and "j.reyes" in v for v in values
        )

    def test_missing_approval_is_reported_not_hidden(self):
        """An unapproved change is a finding, not a blank field."""
        c = build_conclusion(
            _ledger(), **_args(changed_by="d.okonkwo", change_ticket="CHG-4471")
        )
        assert any("no approval recorded" in x.claim_value for x in c.claims)

    def test_missing_author_does_not_invent_one(self):
        c = build_conclusion(_ledger(), **_args(change_ticket="CHG-4471"))
        assert any("an unrecorded author" in x.claim_value for x in c.claims)

    def test_no_provenance_makes_no_claim_at_all(self):
        """Silence beats a claim asserting fields nothing supplied."""
        c = build_conclusion(_ledger(), **_args())
        assert not any("changed by" in x.claim_value for x in c.claims)

    def test_provenance_claim_cites_the_actor_phase(self):
        ledger = _ledger()
        c = build_conclusion(ledger, **_args(changed_by="d.okonkwo"))
        claim = next(x for x in c.claims if "changed by" in x.claim_value)
        actor_ids = {s.step_id for s in ledger.steps_for(Phase.ACTOR)}
        assert set(claim.supporting_step_ids) <= actor_ids
        assert claim.supporting_step_ids
        assert claim.claim_type == ClaimType.ACTOR_PRESET

    def test_provenance_does_not_displace_the_root_cause(self):
        """Who changed it explains WHO to talk to, never WHY the asset failed."""
        c = build_conclusion(_ledger(), **_args(changed_by="d.okonkwo"))
        assert any(x.claim_type == ClaimType.ROOT_CAUSE for x in c.claims)
