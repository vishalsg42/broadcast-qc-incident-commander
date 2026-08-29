#!/usr/bin/env python
"""Run every fixture N times and publish honest k/n accuracy.

    python scripts/evaluate.py --runs 5

Each fixture has a different oracle and therefore a different question, so the
results are reported with SEPARATE denominators rather than collapsed into one
misleading "accuracy" figure:

  fault       did it attribute the defect to the right stage AND preset version?
  source-bad  did it reject the source and propose NO repair?
  clean       did it take no action at all?

Failures are listed, not summarised away. A table with only successes in it is
not evidence.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from agent.conclusion import build_conclusion
from agent.evidence import (
    ClaimType,
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
from pipeline.stages import PACKAGE, PresetLibrary, run_pipeline

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "pipeline" / "profiles" / "ebu_r128.yaml"

# fixture -> (media, injected package preset, what a correct answer looks like)
CASES = {
    "fault": ("master_good.mp4", "pkg_h264_v7", "attributes package + pkg_h264_v7 v7"),
    "source-bad": ("master_hot.mp4", None, "rejects the source, proposes no repair"),
    "clean": ("master_good.mp4", None, "clears the gate, takes no action"),
}


@dataclass
class Outcome:
    fixture: str
    correct: bool
    detail: str


@dataclass
class Tally:
    passed: int = 0
    total: int = 0
    failures: list[str] = field(default_factory=list)

    def add(self, o: Outcome) -> None:
        self.total += 1
        if o.correct:
            self.passed += 1
        else:
            self.failures.append(o.detail)


def run_case(
    fixture: str, profile: Profile, allowlist: dict, grafana: str, reasoner_name: str
) -> Outcome:
    media, fault, _ = CASES[fixture]
    telemetry.init()
    try:
        pr = run_pipeline(
            ROOT / "media" / media,
            out_dir="out",
            overrides={PACKAGE: fault} if fault else None,
            black_opts=profile.black_detector_opts,
            profile=profile,
        )
    finally:
        telemetry.shutdown()

    delivered = evaluate(profile, pr.stages[-1].qc)

    if fixture == "clean":
        ok = delivered.status != BLOCKED
        return Outcome(fixture, ok, f"{pr.run_id}: expected PASS, got {delivered.status}")

    if delivered.status != BLOCKED:
        return Outcome(
            fixture, False, f"{pr.run_id}: expected BLOCKED, got {delivered.status}"
        )

    client = GrafanaClient(GrafanaConfig(url=grafana))
    ledger = EvidenceLedger(run_id=f"eval-{pr.run_id}")
    inv = Investigator(client, ledger, run_id=pr.run_id, asset_id=pr.asset_id)
    reasoner = GeminiReasoner() if reasoner_name == "gemini" else ScriptedReasoner()

    client.wait_for_logs(
        f'{{service_name="qc-pipeline"}} | qc_run_id="{pr.run_id}"', expected=3, timeout_s=120
    )

    baseline = inv.gather_baseline()
    reasoner.interpret(ledger, Phase.BASELINE, baseline.summary)
    divergence = inv.gather_divergence()
    reasoner.interpret(ledger, Phase.DIVERGENCE, divergence.summary)

    source_ok = baseline.summary["source_in_spec"]
    failing = divergence.summary["first_failing_stage"]
    preset_id = preset_version = changed_at = cause = None

    if source_ok and failing:
        actor = inv.gather_actor(failing)
        preset_id = actor.summary["preset_id"]
        preset_version = actor.summary["preset_version"]
        changed_at = actor.summary["preset_changed_at"]
        reasoner.interpret(ledger, Phase.ACTOR, actor.summary)
        preset = PresetLibrary.load().get(failing, preset_id)
        cause_result = inv.gather_cause(
            preset.id, {"audio_filter": preset.audio_filter, "changed_at": preset.changed_at}
        )
        cause = f"{preset.audio_filter} sums both channels into each output channel"
        reasoner.interpret(ledger, Phase.CAUSE, cause_result.summary)

    conclusion = build_conclusion(
        ledger,
        source_in_spec=source_ok,
        failing_stage=failing if source_ok else None,
        preset_id=preset_id,
        preset_version=preset_version,
        preset_changed_at=changed_at,
        cause_detail=cause,
        delivery_profile_id=profile.id,
    )
    validation = validate_conclusion(conclusion, ledger, allowlist=allowlist)
    if not validation.ok:
        return Outcome(
            fixture, False, f"{pr.run_id}: validator rejected — {validation.errors[0]}"
        )

    types = {c.claim_type for c in conclusion.claims}
    action = conclusion.proposed_action.action_id if conclusion.proposed_action else None

    if fixture == "source-bad":
        ok = ClaimType.SOURCE_OUT_OF_SPEC in types and action == "escalate_to_human"
        return Outcome(
            fixture,
            ok,
            f"{pr.run_id}: claims={sorted(t.value for t in types)} action={action}",
        )

    # fault: correct stage AND correct preset version
    attributed = next(
        (c for c in conclusion.claims if c.claim_type == ClaimType.ACTOR_PRESET), None
    )
    ok = (
        failing == PACKAGE
        and preset_id == "pkg_h264_v7"
        and preset_version == 7
        and attributed is not None
        and action == "reencode_with_profile"
    )
    return Outcome(
        fixture,
        ok,
        f"{pr.run_id}: stage={failing} preset={preset_id} v{preset_version} action={action}",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--grafana", default="http://localhost:3000")
    ap.add_argument("--reasoner", choices=["scripted", "gemini"], default="scripted")
    ap.add_argument("--out", default="docs/RESULTS.md")
    args = ap.parse_args()

    profile = Profile.load(PROFILE_PATH)
    with PROFILE_PATH.open() as fh:
        allowlist = allowlist_from_profile(yaml.safe_load(fh))

    tallies = {name: Tally() for name in CASES}
    started = time.time()

    for i in range(1, args.runs + 1):
        for fixture in CASES:
            print(f"  run {i}/{args.runs}  {fixture:<12}", end="", flush=True)
            try:
                outcome = run_case(fixture, profile, allowlist, args.grafana, args.reasoner)
            except Exception as exc:
                outcome = Outcome(fixture, False, f"raised {type(exc).__name__}: {exc}")
            tallies[fixture].add(outcome)
            print("ok" if outcome.correct else f"FAIL — {outcome.detail}")

    elapsed = time.time() - started
    report = render(tallies, args, elapsed)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report)
    print(f"\n{report}\nwritten to {args.out}")

    return 0 if all(t.passed == t.total for t in tallies.values()) else 1


def render(tallies: dict[str, Tally], args, elapsed: float) -> str:
    lines = [
        "# Results",
        "",
        f"`{args.runs}` runs per fixture, reasoner `{args.reasoner}`, "
        f"{elapsed / 60:.1f} min total.",
        "",
        "Each fixture answers a different question, so the denominators are kept",
        "separate rather than collapsed into one misleading accuracy figure.",
        "",
        "| Fixture | Correct answer | Result |",
        "|---|---|---|",
    ]
    for name, tally in tallies.items():
        _, _, expectation = CASES[name]
        mark = "✅" if tally.passed == tally.total else "⚠️"
        lines.append(f"| `{name}` | {expectation} | {mark} {tally.passed}/{tally.total} |")

    failures = [(n, f) for n, t in tallies.items() for f in t.failures]
    lines += ["", "## Failures", ""]
    lines += (
        [f"- `{n}` — {f}" for n, f in failures]
        if failures
        else [
            "None in this run. A table with only successes in it is not evidence,",
            "so the failing detail is printed verbatim whenever it occurs.",
        ]
    )
    lines += [
        "",
        "## What is not measured here",
        "",
        "- Whether the *reasoning* is sound. The validator checks that every claim",
        "  cites evidence that exists; it cannot check entailment.",
        "- Robustness to fault types beyond the three fixtures.",
        "",
        f"_Generated by `scripts/evaluate.py --runs {args.runs} --reasoner {args.reasoner}`._",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
