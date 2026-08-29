# Submission - video script and Devpost draft

Two rules that come from a simulated judging panel and are worth obeying:

**Three minutes is almost entirely screen with things happening.** The instinct
running through this whole repository is to explain, qualify and hedge. That
instinct is why the work is good and it will kill the video. Qualifications
belong on Devpost, where a reader has time.

**Agency is only perceivable as contrast.** A judge watching one run cannot tell
a tool-choosing agent from four hardcoded phases - both render as steps in a
list. Two runs reaching *different* answers is the proof. Budget the seconds.

---

## The 3-minute cut

Record with a warm instance. Expect 3–5 takes: the agent picks its own path, so
no two runs are identical.

| Time | On screen | Said |
|---|---|---|
| **0:00–0:15** | The control room, blocked, red | "This is the part of cinema nobody films. A finished master gets rejected on delivery, and somebody has to find out why before the slot." |
| **0:15–0:45** | Run starts. Signal path fills in: −23.0, −22.8, then **−16.8** red | "Real ffmpeg, measuring at every step. The audio was correct here, and correct here, and wrong here. A deterministic gate blocks it - no AI decides compliance, then or ever." |
| **0:45–1:15** | **Agent panel** filling live: read logs → find trace → read settings → **TEST** | "Now the agent. It picks its own tools - through the official Grafana MCP server, read-only. Nobody told it this order." |
| **1:15–1:45** | **Experiment panel.** −22.8 *would be accepted* / −16.8 *would be rejected* | "And it doesn't guess. It re-runs the failing step with the setting that normally runs, and measures. Same file. Same step. The suspect reproduces the fault." ← **the money shot** |
| **1:45–2:05** | Switch fixture → **wrong setting**. Different conclusion, same symptom | "Same symptom. Completely different cause - this preset hasn't changed since June. Somebody selected the wrong one. Rolling back a change would fix nothing." |
| **2:05–2:20** | Netflix profile → **UNMEASURABLE** | "Asked to judge against Netflix, it declines. Netflix measures dialogue only; this tool measures the whole programme. A confident wrong number is worse than no number." |
| **2:20–2:40** | Approve → repair → same gate → **PASS**. Grafana annotation appears | "A human approves. It repairs, re-checks with the *same* gate, and writes back to Grafana." |
| **2:40–3:00** | Grafana trace waterfall, `stage.package` attributes open | "Every claim cites evidence a controller actually gathered. Built on Google Cloud Agent Builder via the ADK, powered by Gemini 2.5 Flash on Vertex AI." |

### Shots to capture separately

- The **refusal** beat, if a take produces one: `conclude - refused: no experiment
  was run`, then the agent running the experiment. That is the single clearest
  frame in the project that something is deciding rather than replaying.
- The Grafana **trace waterfall** with `stage.package` span attributes expanded.
- The four adversarial refusals in the right-hand column.

### Do not

- Explain EBU R128. Say "too loud" and move on.
- Read the claims aloud. Let them sit on screen.
- Show a cold start. Warm the instance first.

---

## Devpost draft

### Elevator pitch

A broadcast delivery is rejected for being too loud. This agent finds out *which
configuration did it, who changed it, and under what ticket* - then proves it by
re-running the step and measuring, rather than by guessing from a config string.

### Inspiration

Automated QC tools already measure everything. Baton, Vidchecker, Venera all
produce a report saying the file is out of spec. None of them tells you **why**,
and none tells you **who to talk to**. The expensive part of a rejected delivery
was never detection - it was attribution, inside an SLA window.

### What it does

1. Runs a three-stage delivery pipeline on real media with real ffmpeg
2. A **deterministic gate** - zero AI - measures and blocks
3. An **agent** investigates: it chooses its own read-only Grafana tools through
   the official Grafana MCP server, follows what the data shows, and forms a
   hypothesis
4. It **tests that hypothesis** by re-running the failing stage with the preset
   that normally runs, and measuring both
5. It proposes a repair from a typed allowlist; **a human approves**
6. The repair is re-validated by the **same gate**, then written back to Grafana
   as an annotation and an IRM incident

### How we built it

Google Cloud Agent Builder via the ADK, Gemini 2.5 Flash on Vertex AI, Cloud Run,
Grafana Cloud (Loki + Tempo + IRM) read through the official Grafana MCP server,
ffmpeg, FastAPI, Next.js.

### Challenges

**The ledger could not mint unique ids.** Step ids were derived from the count of
*interpreted* steps, so an agent running several tools before interpreting any of
them minted `step-01` every time and overwrote each observation's raw result.
Found by an adversarial review of our own plan, before it shipped.

**The demo had a date landmine.** "Changed recently" means within seven days, and
the fixture carried a fixed date - so the hero scenario would have silently
started telling a different story four days before the deadline.

**The model blamed a preset it had not tested.** So `conclude` now refuses an
attribution with no experiment behind it. A preset that ran is not a preset that
caused.

### What we learned

Constraining an agent is not the opposite of making it agentic. The model here
plans freely *because* it cannot execute anything, cannot decide compliance, and
cannot cite evidence it did not cause to exist.

### What's next

Channel-layout conformance, which would have caught this fault more cheaply than
any investigation. A signed QC report artefact, since broadcast delivery is
contractual. Severity levels - real QC has advisories, not just pass and fail.

### Honest limits

- Fixed scenarios, not uploads, so results are reproducible
- The validator proves a claim cites evidence that exists and is of the right
  kind. It does not verify that the reasoning is sound.
- The measured `+6 LU` is a property of the test content, not of the preset:
  identical channels sum to +6.02 dB, decorrelated stereo to about +3
