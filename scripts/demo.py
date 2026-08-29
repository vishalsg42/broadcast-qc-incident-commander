#!/usr/bin/env python
"""End-to-end walkthrough: block -> investigate -> refuse -> approve -> repair -> clear.

    docker compose -f docker/docker-compose.yml up -d
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 python scripts/demo.py

    --fixture source-bad   source arrives out of spec  (expect: no repair proposed)
    --fixture clean        nothing is wrong            (expect: no action)

The three fixtures matter more than the happy path: an agent that only ever finds
a fault is a puppet, and the two negative cases are what prove otherwise.

Findings are scripted here rather than model-generated. The model's job is exactly
these four judgements; everything around them - retrieval, provenance binding,
validation, adjudication, execution - is what this script exercises, and none of
it changes when a model supplies the findings instead.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from dotenv import load_dotenv

from agent.annotations import GrafanaWriter, WriterConfig, annotation_text
from agent.conclusion import (
    adversarial_candidates,
    build_conclusion,
    render_prose,
    screen_candidate,
)
from agent.evidence import (
    EvidenceLedger,
    Phase,
    allowlist_from_profile,
    validate_conclusion,
)
from agent.grafana import GrafanaClient, GrafanaConfig
from agent.investigator import Investigator
from agent.reasoner import GeminiReasoner, ScriptedReasoner
from pipeline import telemetry
from pipeline.policy import BLOCKED, Profile, evaluate
from pipeline.remediation import execute_repair
from pipeline.stages import NORMALIZE, PACKAGE, PresetLibrary, run_pipeline

ROOT = Path(__file__).resolve().parent.parent

# Load .env so the script behaves the same however it is invoked -
# without it, GRAFANA_URL and the tokens are silently absent.
load_dotenv(ROOT / ".env")
PROFILE_PATH = ROOT / "pipeline" / "profiles" / "ebu_r128.yaml"

FIXTURES = {
    "fault": ("master_good.mp4", "pkg_h264_v7", "package preset remaps channels"),
    "source-bad": ("master_hot.mp4", None, "source arrives out of spec"),
    "clean": ("master_good.mp4", None, "nothing is wrong"),
}


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n {title}\n{'=' * 74}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", choices=sorted(FIXTURES), default="fault")
    ap.add_argument("--grafana", default=None, help="override GRAFANA_URL")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument(
        "--reasoner",
        choices=["scripted", "gemini"],
        default="scripted",
        help="scripted is deterministic and offline; gemini needs Vertex AI credentials",
    )
    args = ap.parse_args()

    media, fault, blurb = FIXTURES[args.fixture]
    reasoner = GeminiReasoner() if args.reasoner == "gemini" else ScriptedReasoner()
    profile = Profile.load(PROFILE_PATH)
    with PROFILE_PATH.open() as fh:
        allowlist = allowlist_from_profile(yaml.safe_load(fh))

    rule(f"SCENARIO: {args.fixture} - {blurb}")

    # ---- 1. run the pipeline -------------------------------------------------
    telemetry.init()
    try:
        run = run_pipeline(
            ROOT / "media" / media,
            out_dir=args.out_dir,
            overrides={PACKAGE: fault} if fault else None,
            black_opts=profile.black_detector_opts,
            profile=profile,
        )
    finally:
        telemetry.shutdown()

    print(f"run_id {run.run_id}")
    for stage in run.stages:
        verdict = evaluate(profile, stage.qc)
        print(
            f"  {stage.stage:<10} {stage.preset.id:<24} "
            f"{stage.qc.loudness.integrated_lufs:>7.1f} LUFS  {verdict.status}"
        )

    delivered = evaluate(profile, run.stages[-1].qc)
    if delivered.status != BLOCKED:
        rule("DELIVERY CLEARED - no investigation required")
        print("The gate passed the asset. Nothing to attribute, nothing to repair.")
        return 0

    rule("DELIVERY BLOCKED")
    for check in delivered.failures:
        print(f"  {check.check_id}: {check.message}")
        print(f"    expected {check.expected}")

    # ---- 2. investigate ------------------------------------------------------
    rule("INVESTIGATION - controller gathers, model interprets")
    client = GrafanaClient(GrafanaConfig.from_env(args.grafana))
    ledger = EvidenceLedger(run_id=f"inv-{run.run_id}")
    inv = Investigator(client, ledger, run_id=run.run_id, asset_id=run.asset_id)

    client.wait_for_logs(
        f'{{service_name="qc-pipeline"}} | qc_run_id="{run.run_id}"', expected=3
    )

    baseline = inv.gather_baseline()
    print(f"  BASELINE    {reasoner.interpret(ledger, Phase.BASELINE, baseline.summary)}")

    divergence = inv.gather_divergence()
    print(f"  DIVERGENCE  {reasoner.interpret(ledger, Phase.DIVERGENCE, divergence.summary)}")

    failing = divergence.summary["first_failing_stage"]
    preset_id = preset_version = changed_at = cause_detail = None

    if baseline.summary["source_in_spec"] and failing:
        actor = inv.gather_actor(failing)
        preset_id = actor.summary["preset_id"]
        preset_version = actor.summary["preset_version"]
        changed_at = actor.summary["preset_changed_at"]
        print(f"  ACTOR       {reasoner.interpret(ledger, Phase.ACTOR, actor.summary)}")

        preset = PresetLibrary.load().get(failing, preset_id)
        cause = inv.gather_cause(
            preset.id,
            {
                "audio_filter": preset.audio_filter,
                "description": preset.description,
                "changed_at": preset.changed_at,
            },
        )
        cause_detail = f"{preset.audio_filter} sums both channels into each output channel"
        print(f"  CAUSE       {reasoner.interpret(ledger, Phase.CAUSE, cause.summary)}")

    # ---- 3. the refusal ------------------------------------------------------
    rule("REFUSAL - adversarial candidates through the PRODUCTION validator")
    for candidate in adversarial_candidates(ledger):
        result = screen_candidate(candidate, ledger, allowlist)
        status = "REFUSED" if not result.ok else "!! ACCEPTED !!"
        print(f"  {status:<14} {candidate.name}")
        print(f"                 {candidate.description}")
        for err in result.errors[:1]:
            print(f"                 -> {err}")

    # ---- 4. the real conclusion ---------------------------------------------
    rule("CONCLUSION")
    conclusion = build_conclusion(
        ledger,
        source_in_spec=baseline.summary["source_in_spec"],
        failing_stage=failing if baseline.summary["source_in_spec"] else None,
        preset_id=preset_id,
        preset_version=preset_version,
        preset_changed_at=changed_at,
        cause_detail=cause_detail,
        delivery_profile_id=profile.id,
    )
    validation = validate_conclusion(conclusion, ledger, allowlist=allowlist)
    print(render_prose(conclusion))
    print(
        f"\n  validator: {'ACCEPTED' if validation.ok else 'REJECTED ' + str(validation.errors)}"
    )
    if not validation.ok:
        return 1

    # ---- 5. approval + repair ------------------------------------------------
    action = conclusion.proposed_action
    if action is None or action.action_id == "escalate_to_human":
        rule("NO AUTOMATED REPAIR")
        print("  Escalated to a human. Re-encoding would mask a supplier problem.")
        return 0

    rule("HUMAN APPROVAL")
    print(f"  proposed : {action.action_id}({action.params})")
    print("  approved : yes  [in the UI this is an engineer's click]")

    repair = execute_repair(
        action.action_id,
        action.params,
        source_path=run.stage(NORMALIZE).output_path,
        profile=profile,
        out_dir=args.out_dir,
        allowlist=allowlist,
    )

    rule("RE-VALIDATION - by the same gate that blocked it")
    print(f"  {repair.message}")
    print(f"  new artefact: {repair.output_path}")
    print(f"\n  {'DELIVERY CLEARED' if repair.resolved else 'STILL BLOCKED'}")

    # Write-back happens LAST, with a separate credential, only after the
    # conclusion validated and a human approved.
    rule("WRITE-BACK - separate credential, only after approval")
    writer = GrafanaWriter(WriterConfig.from_env())
    text = annotation_text(
        asset_id=run.asset_id,
        failing_stage=failing,
        preset_id=preset_id,
        preset_version=preset_version,
        measured=run.stages[-1].qc.loudness.integrated_lufs,
        target=profile.loudness_target[0],
        resolved=repair.resolved,
    )
    annotated = writer.annotate(
        text=text,
        tags=["qc", "delivery", f"preset:{preset_id}", f"stage:{failing}"],
        time_ms=int(time.time() * 1000),
    )
    print(f"  annotation : {'OK' if annotated.ok else 'FAILED'} - {annotated.detail}")
    incident = writer.create_incident(
        title=f"Delivery blocked: {run.asset_id} out of spec at {failing}"
    )
    print(f"  incident   : {'OK' if incident.ok else 'skipped'} - {incident.detail}")

    return 0 if repair.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
