"""An agent that plans its own investigation, inside a boundary it cannot cross.

WHAT CHANGED, AND WHAT DELIBERATELY DID NOT
-------------------------------------------
The four-phase investigator ran a fixed sequence and asked the model to describe
each result. The model chose nothing. Two of the phases handed it a dict that
already contained the answer, and the whole system scored the same with the model
replaced by an f-string - which is a fair definition of "not an agent".

Here the model decides what to look at, in what order, when it has enough, and
what to conclude. What it still cannot do is unchanged:

  cannot decide compliance     pipeline/policy.py is a pure function, no AI, and
                               runs before this agent exists and again after any
                               repair
  cannot invent evidence       the CONTROLLER executes every tool and mints the
                               step_id in after_tool_callback, before the result
                               is ever shown to the model. An id it did not cause
                               to exist is not an id it can obtain.
  cannot act                   `conclude` PROPOSES; validate_conclusion screens
                               the action against the profile allowlist; a human
                               approves; remediation re-validates independently
  cannot roam                  its Grafana surface is four read-only tools out of
                               the server's 74, and the write path is a different
                               module with a different credential

So the thesis moves from "the model interprets" to "the model plans, hypothesises
and tests" without giving up a single boundary.

THE HONEST LIMIT, RESTATED
--------------------------
`validate_conclusion` checks that a cited step EXISTS. It cannot check that the
step supports the claim attached to it. Letting the model author its own
citations makes that gap matter more than it did when the controller attached
them, so CLAIM_SOURCES below binds each claim type to the evidence it is allowed
to rest on. That is a real constraint, not a restatement of the schema - but it
still does not verify reasoning, and nothing here should be read as if it does.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field

from pipeline.experiment import ExperimentError, compare_presets
from pipeline.stages import INGEST, INGEST_PRESET, PresetLibrary

from .evidence import (
    Claim,
    ClaimType,
    Conclusion,
    EvidenceLedger,
    Phase,
    ProposedAction,
    ValidationResult,
    validate_conclusion,
)
from .grafana_mcp import build_toolset

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"

# One LLM call per step of the tool loop. Enough to look around, run an
# experiment and conclude; low enough that a wandering model ends the run rather
# than the run ending the demo. Exceeding it is handled, not crashed on.
MAX_LLM_CALLS = 14

# The investigation must actually happen before it can be concluded: one query
# and an assertion is not an investigation, and min_length=1 on citations is not
# a standard of evidence.
#
# Two, not three. A higher floor is unreachable for a fault that arrived with the
# source - there is no preset to look up and no earlier version to test, so the
# agent could gather forever and never clear it. Attribution to a preset is
# gated separately, by the requirement to test it, which is the constraint that
# actually protects anything.
MIN_SOURCES_BEFORE_CONCLUDING = 2

# How many times the model may re-submit a rejected conclusion. Validator errors
# are returned to it so it can correct itself, which is also exactly the shape
# that burns the whole budget on retries.
MAX_CONCLUDE_ATTEMPTS = 5

# Running ffmpeg twice is the most expensive thing the agent can ask for.
MAX_EXPERIMENTS = 2

# Hard ceiling on rows any query may return, clamped by the controller whatever
# the model asks for. A one-hour window over a busy pipeline is thousands of log
# lines, and the whole payload would land in the context: slow on camera, and
# the needle lost in the haystack.
MAX_QUERY_ROWS = 25

# Ceiling on what a single tool result contributes to the context. The LEDGER
# still holds the full payload - that is the audit trail and it is not
# negotiable - but the model is shown a bounded view of it.
MAX_TOOL_CHARS = 6000

# Which evidence a claim is allowed to rest on. This is what turns the widened
# Phase enum into a constraint: without it, an EXPERIMENT claim asserting a
# measured delta can cite a preset-definition lookup and validate cleanly, with
# no experiment ever run.
CLAIM_SOURCES: dict[ClaimType, set[Phase]] = {
    # THE ONE THAT MATTERS. A claim asserting a measured result must cite the
    # measurement. Without this, "I tested it and measured +6.1 LU" can cite a
    # preset-definition lookup and validate cleanly with no experiment run -
    # a fabricated number wearing a real citation.
    ClaimType.EXPERIMENT: {Phase.EXPERIMENT},
    # The rest are looser, and deliberately so. They exist to stop a claim
    # resting on evidence that cannot bear on it at all, not to impose one
    # correct way to argue. An ACTOR_PRESET claim citing the experiment is
    # sound - the experiment ran that exact preset and watched what it did.
    ClaimType.SOURCE_IN_SPEC: {Phase.BASELINE, Phase.DIVERGENCE},
    ClaimType.SOURCE_OUT_OF_SPEC: {Phase.BASELINE, Phase.DIVERGENCE},
    ClaimType.DIVERGENCE_STAGE: {Phase.DIVERGENCE, Phase.BASELINE},
    ClaimType.ACTOR_PRESET: {Phase.ACTOR, Phase.CAUSE, Phase.EXPERIMENT},
    ClaimType.ROOT_CAUSE: {Phase.CAUSE, Phase.ACTOR, Phase.EXPERIMENT},
    ClaimType.NO_FAULT: {Phase.BASELINE, Phase.DIVERGENCE},
}

# Which tool an observation came from, for the ledger. Grafana's own tool names
# are mapped onto the vocabulary the rest of the system already speaks.
TOOL_PHASE: dict[str, Phase] = {
    "query_loki_logs": Phase.DIVERGENCE,
    "tempo_traceql-search": Phase.ACTOR,
    "tempo_get-trace": Phase.ACTOR,
    "list_datasources": Phase.BASELINE,
    "get_preset_definition": Phase.CAUSE,
    "run_preset_experiment": Phase.EXPERIMENT,
}

SYSTEM_INSTRUCTION = """\
You are investigating why a broadcast delivery was blocked by a deterministic QC
gate. You did not make that decision and you cannot change it.

Your job is to find out WHY, using the tools you have, and then say so.

How to work:
- Decide for yourself which tools to call and in what order. There is no fixed
  sequence. Follow what the data actually shows.
- Every tool result comes back with a `step_id`. That id is your evidence.
- Quote concrete measured values. Never state a number you were not shown.
- If something rules a theory OUT, that is a real finding. Say so.
- You MUST finish by calling `conclude`. Never end with prose - a conclusion
  that is not submitted is not a conclusion. If the source arrived out of
  spec, say so with SOURCE_OUT_OF_SPEC and propose escalate_to_human. If you
  genuinely cannot determine the cause, conclude that too and escalate.
- Each claim must cite the step_id(s) that support it.

What matters:
- A preset that RAN is not the same as a preset that CHANGED. Check whether it
  changed recently before blaming a change. If it has not changed in months, the
  likelier story is that the wrong preset was selected for this content.
- Not every failure is a loudness failure, and not every failure is caused
  by a preset. Read which check actually failed before forming a theory. A
  defect present from the ingest stage onward arrived with the source and is
  a supplier issue, not a pipeline one.
- If you suspect a preset, you MUST test it with run_preset_experiment
  before blaming it. A preset that ran is not a preset that caused, and
  conclude will refuse an untested attribution.
- When the source arrived in spec and a preset caused the failure, the
  repair is to re-run with the delivery profile - escalate only when no
  pipeline stage is responsible.
- Any measured difference is a property of THIS content, not of the preset.

The only tools that exist are:
  list_datasources, query_loki_logs, list_loki_label_names,
  list_loki_label_values, tempo_traceql-search, tempo_get-trace,
  get_preset_definition, run_preset_experiment, conclude
Calling anything else ends the investigation, so do not guess a name.

Constraints you cannot talk your way around:
- You may only cite step_ids that tools actually returned to you.
- Each claim type may only cite the evidence it is allowed to rest on; conclude
  will tell you if you get this wrong, and you should fix it rather than repeat it.
- You may propose only an action on the allowlist you are given.
"""


@dataclass
class ToolCall:
    """One tool the agent chose to run, and what came back."""

    name: str
    args: dict
    step_id: str | None = None
    ok: bool = True
    error: str | None = None


@dataclass
class InvestigationResult:
    conclusion: Conclusion | None
    validation: ValidationResult | None
    calls: list[ToolCall] = field(default_factory=list)
    llm_calls: int = 0
    budget_exhausted: bool = False
    experiment: dict | None = None

    @property
    def tools_used(self) -> list[str]:
        return [c.name for c in self.calls]


def check_claim_sources(conclusion: Conclusion, ledger: EvidenceLedger) -> list[str]:
    """Every claim must rest on evidence of a kind that can support it.

    The citation validator proves a step exists. This proves the step is the
    right KIND of thing - that a claim about a measured experiment cites the
    experiment, and not a lookup that happened to be nearby.
    """
    errors: list[str] = []
    for i, claim in enumerate(conclusion.claims):
        allowed = CLAIM_SOURCES.get(claim.claim_type)
        if not allowed:
            continue
        phases = {
            step.phase
            for sid in claim.supporting_step_ids
            if (step := ledger.get(sid)) is not None
        }
        if phases and not (phases & allowed):
            errors.append(
                f"claims[{i}] ({claim.claim_type}): cites "
                f"{sorted(p.value for p in phases)} but this claim must rest on "
                f"{sorted(p.value for p in allowed)}"
            )
    return errors


class AutonomousInvestigator:
    """Runs one investigation as a bounded ADK tool loop.

    The controller executes every tool, mints the provenance, and screens the
    conclusion. The model chooses what to look at and what to say about it.
    """

    def __init__(
        self,
        *,
        ledger: EvidenceLedger,
        pipeline_run,
        profile,
        allowlist: dict[str, dict],
        model: str = DEFAULT_MODEL,
        max_llm_calls: int = MAX_LLM_CALLS,
        on_event=None,
    ):
        self.ledger = ledger
        self.pr = pipeline_run
        self.profile = profile
        self.allowlist = allowlist
        self.model = model
        self.max_llm_calls = max_llm_calls
        self._emit = on_event or (lambda *a, **k: None)

        self.calls: list[ToolCall] = []
        self._experiments = 0
        self._conclude_attempts = 0
        self._conclusion: Conclusion | None = None
        self._validation: ValidationResult | None = None
        self._experiment_payload: dict | None = None

    # -- local tools -------------------------------------------------------

    def _tool_get_preset_definition(self, preset_id: str, stage: str) -> dict:
        """Look up what a transcode preset actually does.

        Args:
            preset_id: The preset id, e.g. 'pkg_h264_v7'.
            stage: Which pipeline stage it belongs to: ingest, normalize or package.
        """
        preset = _find_preset(stage, preset_id)
        if preset is None:
            return {
                "ok": False,
                "error": f"no preset {preset_id!r} for stage {stage!r}",
            }
        return {
            "ok": True,
            "preset_id": preset.id,
            "version": preset.version,
            "audio_filter": preset.audio_filter,
            "description": preset.description,
            "changed_at": preset.changed_at,
            "changed_by": preset.changed_by,
            "change_ticket": preset.change_ticket,
            "approved_by": preset.approved_by,
        }

    def _tool_run_preset_experiment(self, suspect_preset_id: str, stage: str) -> dict:
        """Test a suspected preset by re-running the stage and measuring.

        Runs the input that stage actually consumed through the preset that
        normally runs and through the suspect one, then measures both. Use this
        instead of reasoning from a filter string.

        Args:
            suspect_preset_id: The preset you suspect, e.g. 'pkg_h264_v7'.
            stage: The stage it ran in, e.g. 'package'.
        """
        if self._experiments >= MAX_EXPERIMENTS:
            return {
                "ok": False,
                "error": f"experiment budget spent ({MAX_EXPERIMENTS}); "
                "conclude from the evidence you already have",
            }
        library = PresetLibrary.load()
        suspect = _find_preset(stage, suspect_preset_id)
        if suspect is None:
            return {
                "ok": False,
                "error": f"no preset {suspect_preset_id!r} for stage {stage!r}",
            }
        if stage == INGEST:
            return {
                "ok": False,
                "error": (
                    "ingest admits the source unchanged - there is nothing to "
                    "test. A defect present at ingest arrived with the source."
                ),
            }
        control = library.default_for(stage)
        if control.id == suspect.id:
            return {
                "ok": False,
                "error": f"{suspect.id} is the default preset for {stage}; "
                "there is no earlier version to compare it against",
            }
        stage_result = self.pr.stage(stage)
        if stage_result is None:
            return {"ok": False, "error": f"no stage {stage!r} in this run"}

        self._experiments += 1
        try:
            result = compare_presets(
                input_path=stage_result.input_path,
                stage=stage,
                control_preset=control,
                suspect_preset=suspect,
                profile=self.profile,
                black_opts=self.profile.black_detector_opts,
            )
        except ExperimentError as exc:
            return {"ok": False, "error": str(exc)}

        self._experiment_payload = result.to_dict()
        self._emit("experiment", **self._experiment_payload)
        return {"ok": True, **self._experiment_payload}

    def _tool_conclude(
        self, claims_json: str, action_id: str = "", rationale: str = ""
    ) -> dict:
        """Submit your conclusion once you can explain the failure.

        Args:
            claims_json: A JSON array. Each element is an object with
                "claim_type" (one of SOURCE_IN_SPEC, SOURCE_OUT_OF_SPEC,
                DIVERGENCE_STAGE, ACTOR_PRESET, ROOT_CAUSE, NO_FAULT,
                EXPERIMENT), "claim_value" (one sentence quoting the values you
                relied on), "supporting_step_ids" (a non-empty array of step_ids
                tools returned to you) and "confidence" (high, medium or low).
            action_id: The remediation you propose, from the allowlist. Use ""
                to propose nothing.
            rationale: Why that action, in one sentence.
        """
        self._conclude_attempts += 1
        if self._conclude_attempts > MAX_CONCLUDE_ATTEMPTS:
            return {
                "ok": False,
                "error": "too many attempts; stop and leave the conclusion as it is",
            }

        sources = {s.phase for s in self.ledger.steps}
        if len(sources) < MIN_SOURCES_BEFORE_CONCLUDING:
            return {
                "ok": False,
                "error": (
                    f"only {len(sources)} kind(s) of evidence gathered "
                    f"({sorted(p.value for p in sources)}); investigate further "
                    f"before concluding - at least "
                    f"{MIN_SOURCES_BEFORE_CONCLUDING} are required"
                ),
            }

        try:
            raw = json.loads(claims_json)
            claims = [Claim(**c) for c in raw]
        except Exception as exc:  # noqa: BLE001 - reported to the model, not raised
            return {"ok": False, "error": f"claims were not valid: {exc}"}

        # If you are going to blame a preset, you have to have tested it.
        #
        # This is the project's own standard applied to the agent: a preset that
        # RAN is not a preset that CAUSED, and the difference is exactly what the
        # experiment settles. Enforced here rather than asked for in the prompt,
        # because "please test your hypothesis" is a request and this is a
        # requirement.
        blames_preset = any(
            c.claim_type in (ClaimType.ACTOR_PRESET, ClaimType.ROOT_CAUSE) for c in claims
        )
        tested = any(s.phase is Phase.EXPERIMENT for s in self.ledger.steps)
        if blames_preset and not tested:
            return {
                "ok": False,
                "error": (
                    "this conclusion attributes the failure to a preset, but no "
                    "experiment was run. Call run_preset_experiment to test "
                    "whether that preset actually produces this result, then "
                    "conclude. A preset that ran is not a preset that caused."
                ),
            }

        action = None
        if action_id:
            action = ProposedAction(
                action_id=action_id,
                params={"profile_id": self.profile.id}
                if action_id == "reencode_with_profile"
                else {"reason": rationale or "escalated by the investigation"},
                rationale=rationale or "proposed by the investigation",
            )
        try:
            conclusion = Conclusion(claims=claims, proposed_action=action)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"conclusion was not valid: {exc}"}

        validation = validate_conclusion(conclusion, self.ledger, allowlist=self.allowlist)
        source_errors = check_claim_sources(conclusion, self.ledger)
        if not validation.ok or source_errors:
            return {
                "ok": False,
                "error": "; ".join([*validation.errors, *source_errors])[:900],
            }

        self._conclusion = conclusion
        self._validation = validation
        return {"ok": True, "accepted": True, "claims": len(conclusion.claims)}

    # -- provenance --------------------------------------------------------

    def _before_tool(self, tool, args, tool_context):
        """Runs before every executed tool call. Returning a dict skips the tool."""
        self._emit("tool_started", tool=tool.name, args=_short(args))
        return

    def _after_tool(self, tool, args, tool_context, tool_response):
        """Mint provenance for what actually ran, before the model sees the result.

        This is the whole guarantee in one function. The controller - not the
        model - decides that a query happened, what it was, and what id refers to
        it. The `step_id` is then injected into the response, so the only ids the
        model can ever cite are ids it caused to exist by running a real tool.
        """
        payload = _as_dict(tool_response)
        failure = _failure_reason(payload)
        if failure is not None:
            # A refused or failed tool is not evidence, so it mints nothing.
            self.calls.append(ToolCall(tool.name, _short(args), ok=False, error=failure[:300]))
            self._emit("tool_failed", tool=tool.name, error=failure[:200])
            return None

        # conclude() is a submission, not an observation - it must not become
        # evidence that a later claim could then cite. It still reports, or the
        # UI shows the agent's last step running forever.
        if tool.name == "conclude":
            self.calls.append(ToolCall(tool.name, _short(args), ok=True))
            self._emit(
                "tool_result", tool=tool.name, step_id=None, phase=None, summary="accepted"
            )
            return None

        phase = TOOL_PHASE.get(tool.name, Phase.DIVERGENCE)
        query = f"{tool.name}({json.dumps(_short(args), sort_keys=True)[:400]})"
        pending = self.ledger.observe(phase, query, payload)
        # Recorded immediately: the model's reading arrives later inside a claim,
        # and an observation nothing interpreted must still be citable.
        self.ledger.record_interpretation(
            f"Result of {tool.name}.", True, step_id=pending.step_id
        )
        self.calls.append(ToolCall(tool.name, _short(args), step_id=pending.step_id))
        self._emit(
            "tool_result",
            tool=tool.name,
            step_id=pending.step_id,
            phase=phase.value,
            summary=_summarise(payload),
        )
        # The ledger has the whole thing; the model gets a bounded view.
        return {**_bounded(payload), "step_id": pending.step_id}

    # -- the loop ----------------------------------------------------------

    async def _investigate_async(self, prompt: str) -> InvestigationResult:
        from google.adk.agents import LlmAgent
        from google.adk.agents.run_config import RunConfig
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        toolset = build_toolset()

        # Bound methods would be exposed to the model as `_tool_conclude`, since
        # ADK derives the tool name from __name__. These wrappers exist so the
        # model sees the names the instruction tells it to use.
        def get_preset_definition(preset_id: str, stage: str) -> dict:
            """Look up what a transcode preset actually does.

            Args:
                preset_id: The preset id, e.g. 'pkg_h264_v7'.
                stage: Its pipeline stage: ingest, normalize or package.
            """
            return self._tool_get_preset_definition(preset_id, stage)

        def run_preset_experiment(suspect_preset_id: str, stage: str) -> dict:
            """Test a suspected preset by re-running the stage and measuring.

            Runs the input that stage actually consumed through the preset that
            normally runs and through the suspect one, and measures both. Use
            this rather than reasoning from a filter string.

            Args:
                suspect_preset_id: The preset you suspect, e.g. 'pkg_h264_v7'.
                stage: The stage it ran in, e.g. 'package'.
            """
            return self._tool_run_preset_experiment(suspect_preset_id, stage)

        def conclude(claims_json: str, action_id: str = "", rationale: str = "") -> dict:
            """Submit your conclusion once you can explain the failure.

            Args:
                claims_json: A JSON array. Each element has "claim_type" (one of
                    SOURCE_IN_SPEC, SOURCE_OUT_OF_SPEC, DIVERGENCE_STAGE,
                    ACTOR_PRESET, ROOT_CAUSE, NO_FAULT, EXPERIMENT),
                    "claim_value" (one sentence quoting the values you relied
                    on), "supporting_step_ids" (a non-empty array of step_ids
                    tools returned to you) and "confidence" (high/medium/low).
                action_id: The remediation you propose, from the allowlist, or
                    "" to propose none.
                rationale: Why that action, in one sentence.
            """
            return self._tool_conclude(claims_json, action_id, rationale)

        agent = LlmAgent(
            name="qc_investigator",
            model=self.model,
            instruction=SYSTEM_INSTRUCTION,
            tools=[
                toolset,
                get_preset_definition,
                run_preset_experiment,
                conclude,
            ],
            before_tool_callback=self._before_tool,
            after_tool_callback=self._after_tool,
        )
        runner = InMemoryRunner(agent=agent, app_name="qcic")
        session = await runner.session_service.create_session(
            app_name="qcic", user_id="investigator"
        )

        llm_calls = 0
        exhausted = False
        try:
            async for event in runner.run_async(
                user_id="investigator",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
                run_config=RunConfig(max_llm_calls=self.max_llm_calls),
            ):
                if event.content and not event.get_function_responses():
                    llm_calls += 1
        except BaseException as exc:  # noqa: BLE001 - re-raised unless it is the budget
            if not _is_budget_exhausted(exc):
                if _is_unknown_tool(exc):
                    log.warning("agent named a tool that does not exist: %s", exc)
                else:
                    raise
            else:
                exhausted = True
                log.warning("agentic investigation exhausted its LLM call budget")
        finally:
            with contextlib.suppress(Exception):
                await toolset.close()

        return InvestigationResult(
            conclusion=self._conclusion,
            validation=self._validation,
            calls=self.calls,
            llm_calls=llm_calls,
            budget_exhausted=exhausted,
            experiment=self._experiment_payload,
        )

    def investigate(self, prompt: str) -> InvestigationResult:
        """Synchronous entry point - the orchestrator runs one investigation per thread."""
        return asyncio.run(self._investigate_async(prompt))


def _find_preset(stage: str, preset_id: str):
    """Resolve a preset, including the ingest passthrough.

    Ingest runs a real preset that lives as a module constant rather than in
    presets.yaml, so looking it up failed - exactly when the defect is present
    from ingest onward and looking it up is the obvious next move.
    """
    if preset_id == INGEST_PRESET.id:
        return INGEST_PRESET
    try:
        return PresetLibrary.load().get(stage, preset_id)
    except KeyError:
        return None


def build_prompt(
    *,
    pipeline_run,
    profile,
    allowlist: dict,
    delivered_lufs: float,
    grafana_config,
    failed_checks: list[str] | None = None,
) -> str:
    """Everything the agent needs to start, and nothing it should have to guess.

    This gives the agent the SCHEMA, not the answer. A human investigator would
    know that only `service_name` is a Loki label here and the rest is structured
    metadata; without being told, the model writes
    `{service_name="qc-pipeline", qc_run_id="..."}`, gets zero results, and
    spends its budget concluding the telemetry is missing.

    The datasource uids are given for the same reason: left to guess, the model
    invents a plausible uid, gets an error, and burns a third of its calls
    recovering - which reads as confusion on camera rather than autonomy.
    """
    target, tolerance = profile.loudness_target
    run_id = pipeline_run.run_id
    # State WHICH checks failed rather than assuming loudness. The profile also
    # enforces true peak and a black-frame policy, and a prompt that opens with
    # a loudness figure sends the agent hunting for a loudness cause on an asset
    # whose loudness is perfectly in spec.
    if failed_checks:
        failures = "The gate failed these checks:\n" + "".join(
            f"  - {c}\n" for c in failed_checks
        )
    else:
        failures = (
            f"Delivered loudness {delivered_lufs} LUFS against a target of "
            f"{target} +/- {tolerance} LU.\n"
        )
    return (
        f"Delivery run {run_id} (asset {pipeline_run.asset_id}) was BLOCKED by "
        f"the {profile.id} profile.\n"
        f"{failures}\n"
        f"The pipeline ran three stages in order: ingest, normalize, package.\n"
        f"Each stage applied a transcode PRESET. Preset ids look like "
        f"'ingest_passthrough_v1', 'norm_ebu_v3', 'pkg_h264_v6', 'pkg_h264_v7'. "
        f"Note that '{profile.id}' is the delivery PROFILE, not a preset.\n\n"
        f'LOKI  datasourceUid "{grafana_config.loki_uid}"\n'
        f"  The ONLY stream label is service_name. Everything else is structured "
        f"metadata and must be filtered with | after the selector.\n"
        f"  Fields: qc_run_id, qc_stage, qc_verdict, qc_integrated_lufs, "
        f"qc_preset_id, qc_preset_version.\n"
        f"  Every stage of this run, which is where you should start:\n"
        f'    {{service_name="qc-pipeline"}} | qc_run_id="{run_id}"\n'
        f"  Use a start time of now-1h; these logs were written minutes ago.\n\n"
        f'TEMPO  datasourceUid "{grafana_config.tempo_uid}"\n'
        f"  Spans: delivery.run (root) and stage.<name>, with attributes "
        f".qc.run_id, .qc.stage, .qc.preset_id, .qc.preset_version, "
        f".qc.preset_changed_at, .qc.preset_changed_by, .qc.preset_change_ticket, "
        f".qc.preset_approved_by\n"
        f"  Search, then fetch the trace by id to read the span attributes:\n"
        f'    {{ .qc.run_id="{run_id}" }}\n\n'
        f"Allowlisted actions: {sorted(allowlist)}\n\n"
        f"Find out why this delivery was blocked, and conclude."
    )


def _causes(exc: BaseException):
    """Walk the whole cause/context chain - ADK wraps tool errors several deep."""
    seen, stack = set(), [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        stack.extend([e.__cause__, e.__context__])


def _is_budget_exhausted(exc: BaseException) -> bool:
    """Running out of LLM calls is a finding about the investigation, not a crash."""
    from google.adk.agents.invocation_context import LlmCallsLimitExceededError

    return any(isinstance(e, LlmCallsLimitExceededError) for e in _causes(exc))


def _is_unknown_tool(exc: BaseException) -> bool:
    return any(isinstance(e, ValueError) and "not found" in str(e) for e in _causes(exc))


def _failure_reason(payload: dict) -> str | None:
    """Why this tool call failed, or None if it succeeded.

    THE LOCAL TOOLS AND THE MCP TOOLS FAIL DIFFERENTLY, and checking only one
    shape is how a failed call became citable evidence. The local tools return
    `{"ok": False, "error": ...}`. The MCP tools never return `ok` at all - they
    return `{"content": [...], "isError": true}`, and ADK's own source carries a
    helper for the isError/is_error spelling difference between MCP 1.x and 2.x.
    Its graceful-error path returns `{"error": ...}` instead.

    Left unchecked, an errored MCP call minted a step_id and was recorded as
    SUPPORTING. Two failed calls clear the evidence floor, so an agent could
    conclude from a pair of authentication errors and pass every validator: the
    steps exist, and their phases are ones the claim may rest on. That is the
    exact failure this module claims to prevent, arriving through the one door
    nobody checked.
    """
    if payload.get("ok") is False:
        return str(payload.get("error", "tool reported failure"))
    if payload.get("isError") or payload.get("is_error"):
        return _mcp_error_text(payload)
    if "error" in payload:
        return str(payload["error"])
    return None


def _mcp_error_text(payload: dict) -> str:
    """Pull the message out of an MCP error result, which nests it in content."""
    for block in payload.get("content") or []:
        if isinstance(block, dict) and block.get("text"):
            return str(block["text"])
    return "the tool reported an error"


def _as_dict(response) -> dict:
    """MCP tools return varied shapes; normalise without losing the content."""
    if isinstance(response, dict):
        return response
    for attr in ("model_dump", "dict"):
        if hasattr(response, attr):
            with contextlib.suppress(Exception):
                return getattr(response, attr)()
    return {"result": response}


def _bounded(payload: dict) -> dict:
    """A view of a tool result that cannot blow out the context window.

    The full payload is already in the ledger under its raw_result_ref, so
    nothing is lost for audit. What the model sees is capped, and the cap is
    announced rather than silently applied - a truncated result the model
    believes is complete is worse than one it knows is partial.
    """
    text = json.dumps(payload, default=str)
    if len(text) <= MAX_TOOL_CHARS:
        return payload
    return {
        "truncated": True,
        "note": (
            f"This result was {len(text)} characters and has been cut to "
            f"{MAX_TOOL_CHARS}. Narrow the query rather than assuming what is "
            "missing."
        ),
        "partial_result": text[:MAX_TOOL_CHARS],
    }


def _short(args) -> dict:
    """Arguments as recorded - truncated, because a query can be long."""
    out = {}
    for k, v in (args or {}).items():
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        out[k] = text[:300]
    return out


def _summarise(payload: dict) -> str:
    text = json.dumps(payload, default=str)
    return text[:300] + ("..." if len(text) > 300 else "")


__all__ = [
    "CLAIM_SOURCES",
    "MAX_QUERY_ROWS",
    "MAX_TOOL_CHARS",
    "build_prompt",
    "MAX_LLM_CALLS",
    "AutonomousInvestigator",
    "InvestigationResult",
    "ToolCall",
    "check_claim_sources",
]
