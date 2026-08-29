"""Reasoner tests.

Covers the property that matters: the model's ONLY surface is
`record_evidence(finding, supports)`. It cannot name the phase, the query or the
raw result, because the controller bound those before the model was invoked.
"""

from __future__ import annotations

import pytest

from agent.evidence import EvidenceLedger, LedgerError, Phase
from agent.reasoner import (
    PHASE_QUESTIONS,
    ReasoningError,
    ScriptedReasoner,
    _supports,
)


@pytest.fixture
def ledger() -> EvidenceLedger:
    return EvidenceLedger(run_id="run-test")


BASELINE_SUMMARY = {"stage": "ingest", "integrated_lufs": -23.0, "source_in_spec": True}
DIVERGENCE_SUMMARY = {"last_good_stage": "normalize", "first_failing_stage": "package"}
ACTOR_SUMMARY = {
    "stage": "package",
    "preset_id": "pkg_h264_v7",
    "preset_version": 7,
    "preset_changed_at": "2026-08-29T14:02:00Z",
}


class TestScriptedReasoner:
    def test_records_into_the_ledger(self, ledger):
        ledger.observe(Phase.BASELINE, "some query", {"raw": 1})
        ScriptedReasoner().interpret(ledger, Phase.BASELINE, BASELINE_SUMMARY)
        assert len(ledger.steps) == 1
        assert ledger.steps[0].phase is Phase.BASELINE

    def test_cannot_record_without_an_observation(self, ledger):
        """No pending observation means the controller never ran a query."""
        with pytest.raises(ReasoningError, match="no pending observation"):
            ScriptedReasoner().interpret(ledger, Phase.BASELINE, BASELINE_SUMMARY)

    def test_supplied_finding_is_used_verbatim(self, ledger):
        ledger.observe(Phase.ACTOR, "q", {})
        text = "Package ran pkg_h264_v7 v7."
        ScriptedReasoner({Phase.ACTOR: text}).interpret(ledger, Phase.ACTOR, ACTOR_SUMMARY)
        assert ledger.steps[0].finding == text

    def test_provenance_comes_from_the_controller_not_the_finding(self, ledger):
        ledger.observe(Phase.ACTOR, 'TRACEQL {name="delivery.run"}', {"spans": []})
        ScriptedReasoner().interpret(ledger, Phase.ACTOR, ACTOR_SUMMARY)
        step = ledger.steps[0]
        assert step.query_used == 'TRACEQL {name="delivery.run"}'
        assert step.raw_result_ref.startswith("raw://run-test/")

    def test_default_description_quotes_the_measurement(self, ledger):
        ledger.observe(Phase.BASELINE, "q", {})
        finding = ScriptedReasoner().interpret(ledger, Phase.BASELINE, BASELINE_SUMMARY)
        assert "-23.0" in finding


class TestSupportsSemantics:
    def test_clean_source_supports_a_pipeline_fault(self):
        assert _supports(Phase.BASELINE, {"source_in_spec": True}) is True

    def test_dirty_source_refutes_a_pipeline_fault(self):
        """Ruling something out is a finding, and must be recorded as such."""
        assert _supports(Phase.BASELINE, {"source_in_spec": False}) is False

    def test_no_failing_stage_refutes(self):
        assert _supports(Phase.DIVERGENCE, {"first_failing_stage": None}) is False


class TestPromptSurface:
    def test_every_phase_has_a_fixed_question(self):
        """Questions are controller-authored, never model-authored."""
        assert set(PHASE_QUESTIONS) == set(Phase)
        assert all(q.endswith("?") for q in PHASE_QUESTIONS.values())


class TestFullLoopOffline:
    def test_four_phases_recorded_through_the_reasoner(self, ledger):
        reasoner = ScriptedReasoner()
        for phase, summary in [
            (Phase.BASELINE, BASELINE_SUMMARY),
            (Phase.DIVERGENCE, DIVERGENCE_SUMMARY),
            (Phase.ACTOR, ACTOR_SUMMARY),
            (Phase.CAUSE, {"preset_id": "pkg_h264_v7", "audio_filter": "pan=stereo|c0=c0+c1"}),
        ]:
            ledger.observe(phase, f"query::{phase.value}", {"phase": phase.value})
            reasoner.interpret(ledger, phase, summary)

        assert len(ledger.steps) == 4
        assert all(ledger.phase_complete(p) for p in Phase)
        assert [s.phase for s in ledger.steps] == list(Phase)

    def test_second_interpretation_without_a_new_query_is_refused(self, ledger):
        ledger.observe(Phase.BASELINE, "q", {})
        ScriptedReasoner().interpret(ledger, Phase.BASELINE, BASELINE_SUMMARY)
        with pytest.raises((ReasoningError, LedgerError)):
            ScriptedReasoner().interpret(ledger, Phase.BASELINE, BASELINE_SUMMARY)


class TestCredentialFailure:
    """Vertex auth failures must be actionable and must not fabricate evidence.

    Google's own message is `DefaultCredentialsError: File ... was not found`,
    which never mentions authentication and sends people hunting for a missing
    data file. It is the first error anyone hits.
    """

    def test_recognises_however_google_auth_phrases_it(self):
        from google.auth.exceptions import DefaultCredentialsError

        from agent.reasoner import _is_credentials_failure

        assert _is_credentials_failure(DefaultCredentialsError("File x was not found."))
        assert _is_credentials_failure(
            RuntimeError("Could not automatically determine credentials")
        )
        assert _is_credentials_failure(RuntimeError("UNAUTHENTICATED: bad token"))
        assert not _is_credentials_failure(ValueError("loudness out of range"))

    def test_message_names_the_command_that_fixes_it(self, monkeypatch):
        from agent.reasoner import _credentials_error

        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "hackathon-506915")
        message = str(_credentials_error(RuntimeError("boom")))
        assert "./scripts/login.sh" in message
        assert "hackathon-506915" in message, "must echo the config it actually used"

    def test_a_failed_model_call_records_no_evidence(self, ledger):
        """A credential failure must not leave phantom evidence behind."""
        ledger.observe(Phase.BASELINE, "q", {})
        assert len(ledger.steps) == 0
