# Broadcast QC Incident Commander

**Automated QC tells you *what* failed. It does not establish *which stage and
which configuration* introduced it.** This closes that gap.

A media deliverable is measured with real ffmpeg at every pipeline stage. A
deterministic policy engine blocks it when it falls out of the buyer's spec. An
agent then investigates across Loki and Tempo to attribute the defect to a
pipeline stage and a **preset version**, proposes a repair from a typed
allowlist, an engineer approves, the repair runs, and **the same policy engine**
re-validates.

> **The model interprets and proposes. Deterministic code gathers, adjudicates
> and executes.**

---

## The hero fault

The interesting failure is not the obvious one. Here, the normaliser does its job
*correctly* and the defect appears one stage later:

| Stage | Preset | Integrated loudness | Verdict |
|---|---|---|---|
| ingest | `ingest_passthrough_v1` | **−23.0 LUFS** | PASS — source arrived in spec |
| normalize | `norm_ebu_v3` | **−22.8 LUFS** | PASS — normalisation was correct |
| package | `pkg_h264_v7` | **−16.8 LUFS** | **BLOCKED** |

`pkg_h264_v7` (changed `2026-08-29T14:02:00Z`) applies
`pan=stereo|c0=c0+c1|c1=c0+c1` — an unconditional downmix that sums an
already-stereo bed into both output channels. You cannot guess this from the
failing number: the source was fine and the normaliser was fine. The
investigation has to walk the stages.

**Attribution targets a preset version, not a worker.** Real facilities do not
hunt for a broken machine; they hunt for which transcode preset changed, and
when.

---

## What makes the conclusion checkable

Most agent demos investigate a fabricated fault and produce a plausible narrative
nobody can verify. Here there is ground truth at every step:

- **ffmpeg produces a real number.** `ebur128` and `blackdetect` decode the file.
- **A YAML profile decides.** `pipeline/profiles/ebu_r128.yaml` is the authority
  on pass/fail — not the code, and never the model.
- **The same code that blocked the asset clears the repaired one.** If the agent
  were wrong, re-validation would say so.

### The controller binds provenance; the model only interprets

`EvidenceLedger.observe()` is called by the controller **after** a query runs,
binding the phase, the query text, its hash, and a reference to the raw response.
The model's entire surface is one tool:

```python
record_evidence(finding: str, supports: bool)
```

It cannot name the phase, the query, or the step id. Without this, a model can
emit a perfectly schema-valid evidence record **citing a query it never ran**,
and the integrity story is theatre. `tests/test_adk_contract.py` asserts the
generated function declaration exposes exactly those two fields.

### The agent has no execution authority

Three actions exist, declared in the delivery profile. The agent names an
`action_id` and supplies typed parameters; it never emits a command string.
`pipeline/remediation.py` re-validates every request before running anything, and
repairs always write a **new** artefact rather than overwriting the input.

### Honest limits

- **Schema validity proves shape, not entailment.** The validator catches
  uncited, mis-cited and fabricated citations. It does not verify that the
  reasoning is sound.
- **Correlation is not causation.** A preset changing shortly before a failure is
  correlation. The conclusion is deliberately hedged — *"most likely introducing
  configuration"* — and its weight comes from what the preset *does*.
- **Three stages stand in for a nine-stage workflow** (conform, colour, mix,
  mastering, versioning, transcode, wrap, package, deliver).
- **Black policy covers explicitly scheduled segments only.** Content-aware black
  classification is a separate subsystem and is out of scope.

---

## Black is a policy, not a boolean

Deliverables *mandate* black: head black, bars and tone, a slate, a 2-pop, black
between programme parts, black at ad-break positions. A check that failed on any
black frame would reject nearly every legitimate master in existence.

```yaml
black:
  permitted_regions:
    - id: head_black
      required: true          # 0–10s; its ABSENCE is also a defect
    - id: tail_black
      start_s: -2.0           # relative to end of file
  body:
    max_contiguous_black_s: 1.0
```

Which is why a deterministic profile engine beats a model eyeballing frames.

### Standards, stated correctly

- **EBU R128** is an EBU *recommendation*, widely adopted by European
  broadcasters and national regimes — **not EU law**.
- **The CALM Act** governs the loudness of **commercial advertisements**, via FCC
  rules incorporating ATSC A/85. It is not blanket programme-delivery law.
- **Netflix's ≈ −27 LKFS is dialogue-gated** — a different measurement that
  `ebur128` does not report. It needs its own profile, not a changed number.

Real rejection causes, in rough order of frequency: audio track layout and
channel mapping, caption conformance, metadata, timecode discontinuity, wrapper
conformance (AS-11 DPP, IMF), illegal video levels, PSE/Harding. Loudness is
among the *least* painful — one pass to measure, one to fix. The expensive part
was never detection; it was attribution.

---

## Architecture

```
                          ┌──────────────────────────────┐
  media ──► ingest ──► normalize ──► package ──►  delivery gate  (ZERO AI)
              │           │            │        └──────────┬───────────────┘
              └───────────┴────────────┘                   │ BLOCKED
                    OpenTelemetry                          ▼
              ┌───────────────────────────┐    ┌──────────────────────┐
              │ Tempo   asset journey     │◄───│  investigation       │
              │         1 trace, 3 spans  │    │  BASELINE            │
              │         + preset version  │    │  DIVERGENCE          │
              │ Loki    QC observations   │◄───│  ACTOR    (Tempo)    │
              │         + preset logs     │    │  CAUSE               │
              └───────────────────────────┘    └──────────┬───────────┘
                                                 controller gathers
                                                 model interprets
                                                          ▼
                          citation validator ──► immutable proposal
                                                          ▼
                                              engineer approves (separate request)
                                                          ▼
                        allowlisted repair ──► NEW artefact ──► SAME gate ──► PASS
                                                          ▼
                                        Grafana annotation + IRM incident
                                        (separate, write-scoped credential)
```

**Signal routing is deliberate.** QC results are *test records*, not operational
time-series — pushing per-measurement values into Prometheus keyed by `asset_id`
is a cardinality anti-pattern. Measurements go to **Loki**; the asset's journey
goes to **Tempo**; only aggregate health would belong in metrics.

**Every log line carries `trace_id` and `span_id`.** Without them the traces and
the logs are two disconnected piles and there is no investigation to run.

---

## Results

Three fixtures, three runs each, **on Gemini 2.5 Flash via Vertex AI** — the
model writing every finding. The two negative cases matter more than the happy
one: an agent that only ever finds a fault is a puppet.

**9/9 correct.** Regenerate with `python scripts/evaluate.py --runs 3
--reasoner gemini`; the full report is in [`docs/RESULTS.md`](docs/RESULTS.md).

| Fixture | Expected | Result |
|---|---|---|
| `fault` | attribute to `pkg_h264_v7`, repair, clear | ✅ blocked → attributed → repaired → **cleared** |
| `source-bad` | reject the source, propose **no** repair | ✅ escalated; re-encoding would mask a supplier problem |
| `clean` | no investigation, no action | ✅ passed the gate, nothing to attribute |

**Refusals** — four adversarial conclusions, run through the *same* validator as
the real path, so the refusal is reproducible rather than dependent on the model
misbehaving:

| Candidate | Refused because |
|---|---|
| `fabricated_citation` | cites unknown step `step-99` |
| `laundered_citation` | one real citation smuggling `step-42` alongside |
| `off_allowlist_action` | `run_shell_command` is not on the allowlist |
| `out_of_range_parameter` | `target_lufs=-60.0` below min `-31.0` |

---

## Running it

```bash
./scripts/make_fixtures.sh                          # reproducible test media
docker compose -f docker/docker-compose.yml up -d   # Grafana + Loki + Tempo

export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
python scripts/demo.py --fixture fault              # the whole loop in a terminal
python scripts/demo.py --fixture source-bad         # escalates, proposes no repair
python scripts/demo.py --fixture clean              # no investigation

uvicorn server.app:app --port 8080                  # control room API
cd ui && npm run dev                                # http://localhost:3001
```

### Tests

```bash
pytest -m "not media and not integration"   #  ~3s   the iteration loop
pytest -m "not integration"                 #  ~2m   adds real ffmpeg
pytest                                      #  ~4m   adds live Loki/Tempo
```

---

## The control room

One screen. The judge never has to open a Grafana tab.

SMPTE 75% colour bars form the progress spine — the asset takes exactly seven
steps here (three pipeline stages, four investigation phases) and 75% bars have
exactly seven segments, so the most recognisable artifact in broadcast doubles as
run status. Loudness is shown on a real EBU R128 meter with the target band
drawn, because that is the instrument an engineer reads. The allowlist stays on
screen throughout, so the constraint is visible rather than asserted at the end.

---

## Stack

| | |
|---|---|
| Agent | Google ADK 2.8.0, Gemini via Vertex AI |
| Partner | Grafana Cloud — Loki, Tempo, annotations, IRM |
| Measurement | ffmpeg `ebur128`, `blackdetect` |
| Backend | Python 3.12, FastAPI, OpenTelemetry |
| Frontend | Next.js 15, Tailwind 4, TanStack Query + Table |

Licensed under Apache-2.0.

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — trust boundaries, signal routing,
  failure modes, and what this design deliberately does not do.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — why it is shaped this way,
  including the decisions that were wrong first time and what changed them.
- [`docs/RESULTS.md`](docs/RESULTS.md) — generated by `scripts/evaluate.py`.
