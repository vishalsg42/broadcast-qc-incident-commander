# Plan - from narrated script to a genuine agent

## The problem, stated without flattery

The system currently runs four hardcoded phases in a fixed order and asks a
frontier model to describe each result. Two facts make this indefensible in an
*agentic* competition:

1. **`scripts/evaluate.py --reasoner scripted` scores 3/3 with no AI at all.**
   The LLM is not load-bearing. Remove Gemini and the verdict, the attribution,
   the repair and the write-back all still work.
2. **BASELINE and DIVERGENCE pass the model a dict that already contains the
   answer** (`source_in_spec: true`, `first_failing_stage: "package"`). We pay a
   frontier model to rephrase a boolean.

A judge grading "agentic" is entitled to call this a Python script with an LLM
bolted on. They would be right.

## What we are NOT giving up

The architecture's guarantees are the reason it is worth defending. All four
survive this change without exception:

| Guarantee | How it survives |
|---|---|
| The model cannot fabricate evidence | The controller executes every tool call and mints the `step_id`. A citation naming a step the controller never created is rejected by the existing validator. |
| The model cannot act | It proposes. `validate_conclusion` screens the action against the profile allowlist. A human approves. |
| The model cannot decide pass/fail | `policy.evaluate()` remains pure deterministic code, called before and after repair. |
| The model cannot roam | Tools are typed, enumerated, read-only, and parameter-validated. |

**The thesis gets stronger, not weaker.** It moves from:

> The model interprets and proposes. Deterministic code gathers, adjudicates and executes.

to:

> **The model plans, hypothesises and tests. Deterministic code executes every
> query, records what was actually run, adjudicates the result, and holds the
> only key to any action.**

That is a better sentence, and it is the difference between constrained agency
and no agency.

---

## Phase A - Causal verification by experiment (half day)

**The single best change in this plan.** It fixes the sharpest criticism, uses
infrastructure that already exists, and cannot destabilise the running system
because it only adds a step.

Today the CAUSE phase looks at the string `pan=stereo|c0=c0+c1|c1=c0+c1` and
reasons that it probably raised the loudness. That is an educated guess about a
string. We already hedge the wording - the claim says *"Most likely introducing
configuration"* at medium confidence - but hedging is a mitigation, not evidence.

**New tool: `simulate_audio_filter(preset_id)`**

1. Take a 5-second slice of the **ingest** artefact (known in-spec)
2. Measure its loudness with `ebur128`
3. Apply the suspect preset's filter to that slice alone
4. Measure again
5. Return `{before, after, delta_lu, seconds_sampled}`

The claim becomes measured rather than inferred:

> "I suspected `pkg_h264_v7`. I ran its filter on a 5-second sample of the
> in-spec ingest artefact: **−23.0 → −16.9, a jump of +6.1 LU**, consistent with
> the +6.2 LU measured on the delivered asset."

Notes that keep it honest:
- It writes to a temp path and **never touches the deliverable**. It is an
  experiment, not a repair.
- It is a *consistency* check, not proof of sole causation, and must be worded
  that way. Two independent faults could sum to the same number.
- It runs on real audio through real ffmpeg. Nothing is simulated in the
  hand-wavy sense.

**Files:** `pipeline/experiment.py` (new), `agent/investigator.py`,
`agent/conclusion.py`, `ui/src/components/Panels.tsx`.

**Stop-here value:** even if nothing else in this plan happens, the weakest
claim in the system becomes a measurement.

---

## Phase B - A read-only toolbox and a real agent loop (1 day)

Replace the fixed four phases with a bounded agent that chooses what to look at.

### The toolbox

Every tool is read-only, typed, controller-executed and ledger-bound:

| Tool | Answers |
|---|---|
| `get_source_measurement(run_id)` | Did it arrive broken? |
| `get_stage_measurements(run_id)` | Where did the numbers move? |
| `get_trace_spans(run_id)` | Which presets ran, with change provenance |
| `get_preset_definition(preset_id)` | What does that preset actually do? |
| `get_preset_change_history(stage)` | What changed recently, and who by? |
| `get_pipeline_errors(run_id)` | **Did anything actually fail?** |
| `get_worker_health(run_id)` | Host and resource signals |
| `simulate_audio_filter(preset_id)` | Does that filter really do this? (Phase A) |
| `conclude(claims, action)` | I can explain it; here is the evidence |

The last three are what make it an agent rather than a menu: it can run an
experiment, it can discover a fault that has nothing to do with presets, and it
decides when it has enough.

### The loop

- One ADK `LlmAgent` holding the toolbox
- Instruction: investigate why this delivery was blocked; ground every claim in
  tool results; call `conclude` when you can explain it
- **Bounded**: max 12 tool calls, max wall-clock, max one `conclude`
- The controller executes each call, mints the `step_id`, records the exact
  query, and returns the result
- `conclude()` output goes through the **existing** `validate_conclusion`

`build_conclusion()` stays as the deterministic path, so `--reasoner scripted`
keeps working unchanged. The agent becomes `--reasoner agentic`, a third option
alongside `scripted` and `gemini`.

**Risk:** this touches the centre of a working system. It is gated behind a flag
precisely so the working paths stay working.

---

## Phase C - Prove it can pivot (half day)

**Without this, "it adapts" is an unverified claim, and the whole exercise is
theatre of a different kind.**

Add a fixture whose fault is **not** a preset change - a truncated source, or a
stage that errors. Against it:

- the old fixed loop asks "which preset ran?" and confidently blames a preset
  that is innocent
- the agent calls `get_pipeline_errors`, finds the real failure, and concludes
  something different

That contrast, demonstrated side by side, is the proof. It is also the single
most persuasive fifteen seconds available for the video.

**Files:** `scripts/make_fixtures.sh`, `pipeline/presets.yaml` or a new failure
injection, `server/orchestrator.py` (`FIXTURES`), `scripts/evaluate.py`.

---

## Phase D - Surface, evaluate, document (half day)

- **UI**: show the investigation as it happens - each tool the agent chose, why,
  and what came back. The evidence table already does most of this; it gains a
  "tool chosen by the agent" column and the experiment result gets its own panel.
- **Evaluation**: `scripts/evaluate.py` re-run across all fixtures for
  `scripted` / `gemini` / `agentic`, reporting honest k/n per fixture. **A drop
  in reliability must be reported, not hidden** - that comparison is itself a
  finding worth publishing.
- **Docs**: D27 recording why autonomy was restored and what still constrains it.

---

## Explicitly NOT doing: fleet-wide blast radius

The suggestion is good in principle and wrong for us right now.

Our Grafana holds telemetry for the runs we have actually made. **There is no
fleet.** To report "14 other assets are affected" we would have to manufacture
those 14 runs - fabricating evidence, in a project whose entire pitch is that it
refuses to fabricate evidence. A judge who spots that has found something much
worse than a missing feature.

If it is built later it must: be named **exposed**, never *affected*; report the
real count even when that count is 2; and never imply the others are broken.

---

## Order, and why

| Phase | Cost | Risk | Value if we stop here |
|---|---|---|---|
| **A** - experiment | 0.5d | very low | Weakest claim becomes measured |
| **B** - agent loop | 1d | **high** | It is genuinely an agent |
| **C** - pivot proof | 0.5d | low | The agency is demonstrated, not asserted |
| **D** - surface | 0.5d | low | Judges can see it |

**A first, alone.** It is additive and independently valuable, so the system is
strictly better even if B is abandoned.

**Then a spike before B**: confirm ADK 2.8.0 runs a genuine multi-turn tool loop
with the controller mediating each call. Everything in B rests on that, and it
is currently an assumption, not a verified fact. If the spike fails, B is
re-planned rather than forced.

## Verification

- `pytest -m "not media and not integration"` stays green and under 5s
- Every phase keeps `--reasoner scripted` working for reproducible demo takes
- `evaluate.py` reports honest per-fixture k/n for all three reasoners
- The agent cannot cite a step the controller did not create - an adversarial
  test asserts this against the new loop, not just the old one
- The experiment never writes to the delivered artefact path

## The trade-off, stated plainly

This is roughly 2.5 days. The 3-minute video is not started, judges weight it
most heavily, and the deadline is **9 September**.

The case for spending it: "we deliberately removed autonomy for safety" is a
weak pitch when the fix is two days of work, and a judge may simply not believe
the constraint was deliberate. The case against: a working system is worth more
than an ambitious broken one, and non-determinism arrives right before filming.

Mitigation for both: every phase is independently shippable, and `scripted`
remains a reproducible path for recording.
