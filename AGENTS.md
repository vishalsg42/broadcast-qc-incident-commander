# AGENTS.md

Instructions for AI coding agents working in this repository.

## What this is

**Broadcast QC Incident Commander.** Finished media deliverables must pass a
buyer's technical spec before delivery. Automated QC products already tell you
*what* failed. Nothing tells you *which pipeline stage and which preset version*
introduced it. This project closes that gap.

Pipeline: `ingest -> normalize -> package`. Real ffmpeg measures each stage. A
deterministic policy engine blocks a failing deliverable. An agent investigates
across telemetry to attribute the fault to a stage and a preset version, proposes
a repair from a typed allowlist, a human approves, the repair executes, and the
**same** policy engine re-validates.

## The architectural rule that governs everything

> **The model interprets and proposes. Deterministic code gathers, adjudicates,
> and executes.**

Concretely, and these are not negotiable:

1. **`pipeline/policy.py` contains zero AI and decides pass/fail alone.** The
   model may never adjudicate compliance. The code that blocks an asset is the
   same code that clears the repaired one — that is what makes the agent's
   conclusion falsifiable instead of merely plausible.
2. **The controller binds evidence provenance; the model supplies interpretation
   only.** `phase`, `query_used`, `query_hash`, `raw_result_ref`, `step_id`,
   `run_id` and timestamps are written by the code that actually ran the query.
   The model contributes `finding` and `supports`. Without this, a model can emit
   a schema-valid evidence record citing a query it never ran.
3. **The agent has no execution authority.** It names an `action_id` from the
   allowlist and supplies typed parameters. It never emits a command string.
   `pipeline/remediation.py` re-validates every request before running anything.
4. **Faults are data, not code.** They live in `pipeline/presets.yaml` as preset
   versions with `changed_at` timestamps. Never write `if asset_id == "demo_001"`.

## Layout

```
pipeline/
  ffmpeg.py       every subprocess call goes through here (argument building,
                  error handling, the mandatory delivery flags)
  qc.py           measurement only - ebur128, blackdetect. Never decides.
  policy.py       the deterministic gate. ZERO AI.
  stages.py       ingest -> normalize -> package, preset-driven
  remediation.py  allowlisted repair execution + re-validation
  presets.yaml    transcode presets, versioned. Faults live here.
  profiles/       delivery profiles. The profile file is the authority on
                  pass/fail, not the code.
agent/
  evidence.py     evidence ledger, structured claims, citation validator,
                  action allowlist validation
  grafana.py      READ-only Loki/Tempo via the datasource proxy
  annotations.py  WRITE side, separate credential. Never used mid-investigation
  investigator.py the fixed four-phase topology
  reasoner.py     the model-facing surface: record_evidence(finding, supports)
  conclusion.py   claim assembly + the deterministic adversarial candidates
server/
  app.py          FastAPI: POST /api/runs then GET .../events (SSE)
  orchestrator.py run state machine; approval is a separate request
ui/               Next.js 15 + Tailwind 4 + TanStack Query/Table
tests/            pytest. Integration tests use real media and real ffmpeg.
scripts/
  make_fixtures.sh    generates reproducible test media
  activate_env.sh     isolates CLOUDSDK_CONFIG so ADC cannot leak
  guard_env.sh        aborts on a production project
```

## Commands

```bash
source scripts/activate_env.sh     # isolates gcloud/ADC, loads .env, activates venv
./scripts/guard_env.sh             # MUST print OK before any cloud call
./scripts/make_fixtures.sh         # regenerate test media (gitignored)

pytest -m "not media and not integration"   # ~3s  - the iteration loop
pytest -m "not integration"                 # ~2m  - adds real ffmpeg
pytest                                      # ~4m  - adds live Loki/Tempo

python scripts/demo.py --fixture fault        # block -> investigate -> repair
python scripts/demo.py --fixture source-bad   # escalates, proposes NO repair
python scripts/demo.py --fixture clean        # no investigation, no action
python scripts/demo.py --reasoner gemini      # same loop, model writes findings
python scripts/evaluate.py --runs 5           # regenerate docs/RESULTS.md
python scripts/check_grafana.py               # verify BOTH Grafana credentials

# control room (two processes)
uvicorn server.app:app --port 8080            # API + SSE
cd ui && npm run dev                          # Next.js on :3001, proxies /api
python -m pipeline.qc media/master_good.mp4
python -m pipeline.policy media/master_hot.mp4
python -m pipeline.stages media/master_good.mp4 pkg_h264_v7   # inject the fault
```

## Conventions

- **Python 3.12**, pinned deps in `requirements-lock.txt`. Do not bump `google-adk`
  without re-running the suite; ADK's behaviour here is version-sensitive.
- **KISS/DRY.** One responsibility per module. All ffmpeg invocation lives in
  `pipeline/ffmpeg.py`. If you find yourself building an ffmpeg argument list
  anywhere else, put it there instead.
- **No private access across module boundaries.** `Profile` exposes accessors;
  nothing reads `profile._d`.
- **Type hints everywhere**, `from __future__ import annotations` at the top.
- **Pure functions where possible.** `policy.evaluate()` is pure and must stay so.
- **Comments explain *why*, never *what*.** Prefer a comment that records a
  decision or a trap over one that narrates the code.
- **Three test tiers.** Pure unit tests must stay fast enough to run on every
  save; `media` shells out to ffmpeg; `integration` needs the Grafana stack.
  Never let a pure unit test acquire a subprocess or a socket.
- **Tests must prove the guard fires**, not merely that the happy path works. A
  validator with no test showing it reject something is indistinguishable from a
  costume.

## Domain facts that are easy to get wrong

- **Black is a POLICY, not a boolean.** Deliverables *mandate* black: head black
  (~10s), bars and tone, slate, 2-pop, black between parts, ad-break black. A
  profile failing on any black frame would reject nearly every legitimate master.
- **EBU R128** is an EBU *recommendation* widely adopted by European broadcasters
  and national regimes — **not EU law**.
- **The CALM Act** governs the loudness of **commercial advertisements** via FCC
  rules incorporating ATSC A/85. It is not blanket programme-delivery law.
- **Netflix's ~-27 LKFS is dialogue-gated** — a different measurement that
  `ebur128` does not report. It needs its own profile, not a changed number.
- **Integrated loudness only.** Never compare momentary or short-term values
  against an R128 integrated target.
- **Real rejection causes**, in rough order of frequency: audio track layout and
  channel mapping, caption conformance and timing, metadata, timecode
  discontinuity, wrapper conformance (AS-11 DPP, IMF), illegal video levels,
  PSE/Harding photosensitivity. Loudness is among the *least* painful, because it
  is measured in one pass and fixed by a second.
- **Do not overclaim the cost.** The usual outcome of a failed delivery is a QC
  report bounced back with a redelivery request. The real cost is cycle time and
  a lost slot, not automatic contractual penalties.
- **Correlation is not causation.** "Preset v7 appeared at 14:02 and the next
  asset failed" is correlation. Either show the preset diff, or state the
  conclusion with calibrated confidence.

## Recording the demo

- **Do a throwaway run first to warm ingestion.** The first write to a new
  Grafana Cloud Loki stream takes 90s+ because the stream must be created;
  subsequent writes land in well under a second. Recording cold means minutes
  of dead air.
- The UI shows the wait rather than hiding it (`IngestWait`), so even a cold
  run reads as a distributed system doing work rather than a hang.

## Traps already paid for

- **`-movflags +faststart` on every output.** Without it the moov atom trails the
  media, a browser cannot seek or progressively play, and a working demo looks
  broken on camera.
- **Never use the ffmpeg concat *demuxer* with `-c copy` here.** It produced an
  86s file from 45s of input (AAC edit-list accumulation). Use the concat
  *filter* in a single pass.
- **`LoopAgent` is deprecated** in ADK 2.8.0 in favour of Workflow, and it gives
  an iteration count rather than a timeout or a terminal escalation. Use plain
  Python for bounded retry.
- **`output_schema` + `tools`** on one `LlmAgent` constructs fine in ADK 2.8.0 but
  is documented as model-dependent. This project does not rely on it: evidence
  structure comes from **typed tool arguments** instead.
- **A tool must never raise.** ADK turns an exception inside a tool into an
  aborted invocation rather than a retryable response. Return
  `{"ok": False, "error": ...}` — see `safe_record_interpretation`.
- **`gcloud config configurations` does not isolate ADC.** Use `CLOUDSDK_CONFIG`.
- **Quote `OTEL_EXPORTER_OTLP_HEADERS` in `.env`.** Its value contains a space,
  so an unquoted line is truncated by the shell at `Basic` and the credential
  never leaves the process. Grafana answers `401 no credentials provided`,
  which looks like a bad token rather than a quoting bug.
- **A Grafana Cloud stack has SEVERAL Loki datasources** (`-logs`,
  `-alert-state-history`, `-usage-insights`). Picking the first match queries
  the wrong one and returns nothing. Use `scripts/check_grafana.py`, which
  prints every candidate and marks the choice.
- **ADK 2.8 exposes the generated tool schema as `parameters_json_schema`**, not
  the older `parameters` field, which is `None`. ADK also flags this as
  EXPERIMENTAL (`JSON_SCHEMA_FOR_FUNC_DECL`), so re-run
  `tests/test_adk_contract.py` after any ADK bump - it asserts the model-facing
  surface is exactly `finding` + `supports`, which is the guarantee the whole
  provenance design rests on.

## Hard constraints

- **Runtime AI must be Google only** — Gemini via Vertex AI, plus the Grafana
  partner integration. No LangChain, LlamaIndex, CrewAI, DSPy, OpenAI or
  Anthropic anywhere in the dependency tree.
- **Never build against the `nexkard-*` / `*-prod` projects.** `guard_env.sh`
  hard-aborts on these; do not weaken it.
- **Apache-2.0**, public repo.
