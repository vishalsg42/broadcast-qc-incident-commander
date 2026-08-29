"""The model-facing layer: interpreting one phase's evidence.

The model's entire surface is a single tool:

    record_evidence(finding: str, supports: bool)

It cannot name the phase, the query, or the raw result - the controller already
bound those in `EvidenceLedger.observe()` before the model was called. So the
worst a misbehaving model can do here is record a wrong *interpretation* of a
query that genuinely ran. It cannot invent the query, and it cannot invent the
data.

Structure comes from the tool's typed arguments rather than `output_schema`. ADK
documents `output_schema` + `tools` as model-dependent ("only supported by
specific models"), and its samples carry "NO tools parameter here - using
output_schema prevents tool use". Function-calling already enforces the shape, so
we sidestep the question entirely rather than depending on a version-specific
behaviour.

`Reasoner` is a protocol with two implementations: `GeminiReasoner` for the real
thing, and `ScriptedReasoner` so the whole loop is testable offline and the demo
is reproducible without burning tokens.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from .evidence import EvidenceLedger, Phase, safe_record_interpretation

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_ATTEMPTS = 3

PHASE_QUESTIONS: dict[Phase, str] = {
    Phase.BASELINE: "Was the source in spec when it arrived at ingest?",
    Phase.DIVERGENCE: "Which pipeline stage did the asset first fall out of spec at?",
    Phase.ACTOR: "Which preset version ran the failing stage?",
    Phase.CAUSE: "What about that preset explains the measured divergence?",
}

SYSTEM_INSTRUCTION = """\
You are a broadcast delivery QC investigator. You are given the result of ONE
query that has already been executed against observability data, plus the fixed
question that query was meant to answer.

Your job is to interpret that result and nothing else.

Call `record_evidence` exactly once with:
  finding  - one or two sentences stating what this result shows. Quote the
             specific values you are relying on (measurements, stage names,
             preset ids, timestamps). Do not speculate beyond the data shown.
  supports - true if this result supports the emerging explanation of the
             failure, false if it argues against it or rules something out.

Rules:
- Never claim a measurement or identifier that is not present in the result.
- Ruling something OUT is a valuable finding. If the data exonerates a stage,
  say so plainly.
- A preset changing shortly before a failure is correlation. If you assert a
  cause, ground it in what the preset actually does, not merely in its timing.
- Do not propose fixes here. That happens later, from a fixed allowlist.
"""


class ReasoningError(RuntimeError):
    pass


class CredentialsError(ReasoningError):
    """Vertex AI could not authenticate.

    Google's own message for this is `DefaultCredentialsError: File ... was not
    found`, which never mentions authentication and sends people looking for a
    missing data file. This replaces it with the command that fixes it.
    """


_CREDENTIAL_HELP = """\
Vertex AI could not authenticate ({detail}).

Run the one-time login, which is interactive and isolates itself from any
other gcloud account on this machine:

    ./scripts/login.sh

If the message mentions an API KEY, the cause is usually a missing
GOOGLE_GENAI_USE_VERTEXAI=TRUE: google-genai then targets the Gemini Developer
API instead of Vertex, and asks for a key that this project never uses.

It sets CLOUDSDK_CONFIG to ./.gcloud, signs in, selects the project, configures
Application Default Credentials with a quota project, and enables the Vertex AI
API. Verify with ./scripts/guard_env.sh before retrying.

Current: project={project} location={location} use_vertexai={vertex}\
"""


def _credentials_error(exc: Exception) -> CredentialsError:
    return CredentialsError(
        _CREDENTIAL_HELP.format(
            detail=f"{type(exc).__name__}: {exc}",
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "<unset>"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "<unset>"),
            vertex=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "<unset>"),
        )
    )


class Reasoner(Protocol):
    """Turns one phase's retrieved evidence into a recorded interpretation."""

    def interpret(self, ledger: EvidenceLedger, phase: Phase, summary: dict) -> str:
        """Record an interpretation into the ledger. Returns the finding text."""
        ...


class ScriptedReasoner:
    """Deterministic interpretations, for tests and reproducible demo takes.

    Not a mock of the model's reasoning - it exercises the exact same recording
    path (`safe_record_interpretation`), so provenance binding, validation and
    retry behaviour are all genuinely covered.
    """

    def __init__(self, findings: dict[Phase, str] | None = None):
        self._findings = findings or {}

    def interpret(self, ledger: EvidenceLedger, phase: Phase, summary: dict) -> str:
        finding = self._findings.get(phase) or _describe(phase, summary)
        result = safe_record_interpretation(ledger, finding, _supports(phase, summary))
        if not result["ok"]:
            raise ReasoningError(result["error"])
        return finding


class GeminiReasoner:
    """Interprets one phase using Gemini through Google ADK.

    Built per call rather than held open: each phase gets a fresh session so an
    earlier phase's narrative cannot leak into a later one's interpretation.
    """

    def __init__(self, model: str = DEFAULT_MODEL, *, max_attempts: int = MAX_ATTEMPTS):
        self.model = model
        self.max_attempts = max_attempts

    def interpret(self, ledger: EvidenceLedger, phase: Phase, summary: dict) -> str:
        import asyncio

        return asyncio.run(self.interpret_async(ledger, phase, summary))

    async def interpret_async(
        self, ledger: EvidenceLedger, phase: Phase, summary: dict
    ) -> str:
        from google.adk.agents import LlmAgent
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        recorded: list[str] = []

        def record_evidence(finding: str, supports: bool) -> dict:
            """Record your interpretation of the query result you were shown.

            Args:
                finding: One or two sentences stating what this result shows,
                    quoting the specific values you relied on.
                supports: True if this supports the emerging explanation of the
                    failure, False if it argues against it or rules something out.
            """
            result = safe_record_interpretation(ledger, finding, supports)
            if result["ok"]:
                recorded.append(finding)
            return result

        agent = LlmAgent(
            name=f"interpret_{phase.value.lower()}",
            model=self.model,
            instruction=SYSTEM_INSTRUCTION,
            tools=[record_evidence],
        )

        runner = InMemoryRunner(agent=agent, app_name="qcic")
        session = await runner.session_service.create_session(
            app_name="qcic", user_id="investigator"
        )

        prompt = (
            f"Question: {PHASE_QUESTIONS[phase]}\n\n"
            f"Query result:\n{json.dumps(summary, indent=2)}\n\n"
            "Call record_evidence once with your interpretation."
        )

        for _attempt in range(1, self.max_attempts + 1):
            try:
                async for _ in runner.run_async(
                    user_id="investigator",
                    session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
                ):
                    pass
            except Exception as exc:
                if _is_credentials_failure(exc):
                    raise _credentials_error(exc) from exc
                raise
            if recorded:
                return recorded[0]
            # The model answered without calling the tool. Retrying is the
            # controller's decision, not the model's - ADK's SequentialAgent
            # guarantees order, never that a tool was actually called.
            prompt = (
                "You did not call record_evidence. You must call it exactly once. "
                f"Question: {PHASE_QUESTIONS[phase]}\n\n"
                f"Query result:\n{json.dumps(summary, indent=2)}"
            )

        raise ReasoningError(
            f"{phase.value}: model did not record evidence after {self.max_attempts} attempts"
        )


def _is_credentials_failure(exc: Exception) -> bool:
    """Recognise an auth failure however google-auth chose to phrase it."""
    name = type(exc).__name__
    if "Credentials" in name or "Refresh" in name:
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "default credentials",
            "could not automatically determine credentials",
            "was not found",
            "unauthenticated",
            "permission denied on resource project",
            # google-genai falls back to the Developer API when
            # GOOGLE_GENAI_USE_VERTEXAI is unset, and then complains about a
            # missing API key rather than about Vertex credentials.
            "no api key was provided",
            "missing key inputs argument",
        )
    )


# --------------------------------------------------------------------------
# Fallback descriptions, used by ScriptedReasoner when no finding is supplied.
# --------------------------------------------------------------------------


def _describe(phase: Phase, summary: dict) -> str:
    if phase is Phase.BASELINE:
        state = "in spec" if summary.get("source_in_spec") else "ALREADY out of spec"
        return f"Source measured {summary.get('integrated_lufs')} LUFS at ingest: {state}."
    if phase is Phase.DIVERGENCE:
        return (
            f"In spec through {summary.get('last_good_stage')}; first out of spec at "
            f"{summary.get('first_failing_stage')}."
        )
    if phase is Phase.ACTOR:
        return (
            f"Trace shows {summary.get('stage')} executed preset "
            f"{summary.get('preset_id')} v{summary.get('preset_version')} "
            f"(changed {summary.get('preset_changed_at')})."
        )
    return (
        f"Preset {summary.get('preset_id')} applies {summary.get('audio_filter')}, "
        "which alters delivered loudness independently of the normalise stage."
    )


def _supports(phase: Phase, summary: dict) -> bool:
    """Whether the result supports a pipeline-fault explanation."""
    if phase is Phase.BASELINE:
        # A clean source SUPPORTS the pipeline being at fault; a dirty one refutes it.
        return bool(summary.get("source_in_spec"))
    if phase is Phase.DIVERGENCE:
        return summary.get("first_failing_stage") is not None
    return True
