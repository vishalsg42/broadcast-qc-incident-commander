"""D1 probe: does THIS pinned ADK version allow output_schema + tools on one LlmAgent?

The docs warn that combining them "is only supported by specific models, including
Gemini 3.0", and the samples say "NO tools parameter here - using output_schema
prevents tool use". But ADK main has since added a set_model_response sample where
they coexist. So the answer is version- and model-dependent, and the plan hinges on it.

Run this before writing any agent code:

    source scripts/activate_env.sh
    ./scripts/guard_env.sh && python scripts/probe_output_schema_with_tools.py

Outcomes:
  BOTH WORK      -> we may put output_schema and tools on the same agent.
  CONSTRUCT FAIL -> hard block; use the planned design (structure via tool args).
  RUNTIME SKIP   -> agent ignored the tool; use the planned design.

Either way the planned design (controller-bound evidence + record_evidence tool)
remains correct. This only tells us whether a simpler option is also available.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import BaseModel, Field

MODEL = os.environ.get("PROBE_MODEL", "gemini-2.5-flash")

_tool_calls: list[str] = []


def lookup_asset_loudness(asset_id: str) -> dict:
    """Return the measured integrated loudness for an asset.

    Args:
        asset_id: The asset identifier, e.g. "asset-001".
    """
    _tool_calls.append(asset_id)
    return {"asset_id": asset_id, "integrated_lufs": -18.2, "target_lufs": -23.0}


class Verdict(BaseModel):
    asset_id: str = Field(description="The asset that was checked")
    measured_lufs: float = Field(description="Measured integrated loudness")
    in_spec: bool = Field(description="Whether it meets the target")


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("google-adk")
    except Exception as exc:  # pragma: no cover
        return f"<unknown: {exc}>"


async def main() -> int:
    print(f"google-adk    : {_version()}")
    print(f"python        : {sys.version.split()[0]}")
    print(f"model         : {MODEL}")
    print(f"use_vertexai  : {os.environ.get('GOOGLE_GENAI_USE_VERTEXAI', '<unset>')}")
    print(f"project       : {os.environ.get('GOOGLE_CLOUD_PROJECT', '<unset>')}")
    print("-" * 60)

    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    try:
        agent = LlmAgent(
            name="probe",
            model=MODEL,
            instruction=(
                "Call lookup_asset_loudness for the asset the user names, then "
                "report the verdict. in_spec is true only if the measured value is "
                "within 0.5 LU of the target."
            ),
            tools=[lookup_asset_loudness],
            output_schema=Verdict,
        )
        print("CONSTRUCT     : OK (no error combining tools + output_schema)")
    except Exception as exc:
        print(f"CONSTRUCT     : FAILED -> {type(exc).__name__}: {exc}")
        print("\nVERDICT: hard block. Use the planned design (structure via tool args).")
        return 1

    runner = InMemoryRunner(agent=agent, app_name="probe")
    session = await runner.session_service.create_session(app_name="probe", user_id="u1")

    final = None
    try:
        async for event in runner.run_async(
            user_id="u1",
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text="Check asset-001.")]
            ),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final = event.content.parts[0].text
    except Exception as exc:
        print(f"RUNTIME       : FAILED -> {type(exc).__name__}: {exc}")
        print("\nVERDICT: unusable at runtime. Use the planned design.")
        return 1

    print(f"TOOL CALLED   : {bool(_tool_calls)}  {_tool_calls}")
    print(f"FINAL OUTPUT  : {final!r}")

    conformed = False
    if final:
        try:
            Verdict.model_validate_json(final)
            conformed = True
        except Exception:
            conformed = False
    print(f"SCHEMA VALID  : {conformed}")

    print("-" * 60)
    if _tool_calls and conformed:
        print("VERDICT: BOTH WORK on this version+model.")
        print("         Simpler option available, but controller-bound evidence")
        print("         provenance is still required - keep record_evidence.")
        return 0

    print("VERDICT: not usable together (tool skipped or schema not honoured).")
    print("         Use the planned design: structure via typed tool arguments.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
