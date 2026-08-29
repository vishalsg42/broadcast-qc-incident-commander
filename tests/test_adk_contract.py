"""What ADK actually tells Gemini the tool accepts.

This is the load-bearing guarantee of the whole design, and it is invisible in
the Python source: the *generated function declaration* is what the model sees.
If a future refactor adds `phase` or `query` to the signature "for convenience",
the model gains the ability to name a query it never ran and the provenance
story silently becomes theatre. Nothing else in the suite would notice.

Runs without credentials - building the declaration needs no model call.
"""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agent.evidence import EvidenceLedger, Phase, safe_record_interpretation
from agent.reasoner import DEFAULT_MODEL, PHASE_QUESTIONS, SYSTEM_INSTRUCTION


@pytest.fixture
def ledger() -> EvidenceLedger:
    return EvidenceLedger(run_id="contract")


@pytest.fixture
def record(ledger):
    def record_evidence(finding: str, supports: bool) -> dict:
        """Record your interpretation of the query result you were shown.

        Args:
            finding: One or two sentences stating what this result shows.
            supports: True if this supports the emerging explanation.
        """
        return safe_record_interpretation(ledger, finding, supports)

    return record_evidence


@pytest.fixture
def schema(record) -> dict:
    # ADK 2.8 exposes the generated schema as `parameters_json_schema`;
    # the older `parameters` field is None on this version.
    return FunctionTool(record)._get_declaration().parameters_json_schema


class TestModelFacingSchema:
    def test_model_must_supply_finding_and_supports(self, schema):
        assert set(schema["required"]) == {"finding", "supports"}

    def test_types_are_what_we_expect(self, schema):
        assert schema["properties"]["finding"]["type"] == "string"
        assert schema["properties"]["supports"]["type"] == "boolean"

    @pytest.mark.parametrize(
        "forbidden", ["phase", "query", "query_used", "step_id", "run_id", "raw_result_ref"]
    )
    def test_model_cannot_supply_provenance(self, schema, forbidden):
        """The controller binds these. Exposing any of them breaks the guarantee."""
        assert forbidden not in schema["properties"], (
            f"{forbidden!r} is model-supplied - it must be bound by the controller"
        )

    def test_surface_is_exactly_two_fields(self, schema):
        assert set(schema["properties"]) == {"finding", "supports"}


class TestAgentConstruction:
    def test_agent_builds_with_the_tool_and_no_output_schema(self, record):
        agent = LlmAgent(
            name="interpret_baseline",
            model=DEFAULT_MODEL,
            instruction=SYSTEM_INSTRUCTION,
            tools=[record],
        )
        assert len(agent.tools) == 1
        # output_schema + tools is documented as model-dependent in ADK; we get
        # structure from the tool signature instead and must not start relying
        # on it by accident.
        assert agent.output_schema is None

    def test_instruction_forbids_proposing_fixes(self):
        assert "Do not propose fixes here" in SYSTEM_INSTRUCTION

    def test_instruction_treats_ruling_out_as_a_finding(self):
        assert "Ruling something OUT" in SYSTEM_INSTRUCTION

    def test_every_phase_question_is_controller_authored(self):
        assert set(PHASE_QUESTIONS) == set(Phase)


class TestToolBehaviour:
    def test_records_when_an_observation_is_pending(self, ledger, record):
        ledger.observe(Phase.BASELINE, 'LOGQL {job="ingest"}', {"lufs": -23.0})
        assert record("Source in spec at -23.0 LUFS.", True) == {
            "ok": True,
            "step_id": "step-01",
            "phase": "BASELINE",
        }

    def test_returns_error_data_rather_than_raising(self, record):
        """ADK turns a raised exception into an aborted invocation, not a retry."""
        out = record("nothing was queried", True)
        assert out["ok"] is False and "no pending observation" in out["error"]

    def test_controller_owns_the_query_text(self, ledger, record):
        ledger.observe(Phase.ACTOR, 'TRACEQL {name="delivery.run"}', {"spans": []})
        record("package ran pkg_h264_v7", True)
        assert ledger.steps[0].query_used == 'TRACEQL {name="delivery.run"}'
