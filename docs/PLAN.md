# Broadcast QC Incident Commander - Build Plan (rev 3, post-review)

## Context

**Agentic Cinema hackathon**, Grafana Labs track. Solo, full time, new to GCP/ADK.
India eligible (verified). Repo is empty; nothing is built.

**Deadline reality:** Sept 9 2026 2:00 PM PDT = **Sept 10, 2:30 AM IST**.
Working at 2:30 AM after eleven straight days is not a buffer.
→ **Real deadline: end of day Sept 8 IST. Target submission Sept 9, 6 PM IST.**

**Rev 3 exists because** four independent reviews (Codex ×2, a judge simulation, a delivery-risk
review) converged: rev 2 fixed the sequencing but was still ~175 hours of work against ~85–95
productive hours. Probability of shipping complete and polished: **12–30% as written, 45–55%
with the cuts below.** The cuts lose half their value if deferred past today.

**Positioning line** (softened - the universal claim was unnecessary and attackable):
> AQC tells you *what* failed. AQC alone does not establish *which stage and configuration*
> introduced it.

**Core claim:**
> The model interprets and proposes. Deterministic code gathers, adjudicates, and executes.

---

## The five cuts (decide today)

1. **Host ADK on Cloud Run, not Agent Engine.** Removes a second deploy target, the 10-minute
   deploy loop that would poison days 5–6, egress-service-account archaeology, and Agent
   Engine's deploy-time dependency resolution. *~1.5 days.*
   → **Keep agent logic host-agnostic** so this is reversible.
   → ⚠️ **Compliance risk: rules require "powered by Gemini and Google Cloud Agent Builder."**
   ADK ships under Agent Builder, so ADK-on-Cloud-Run is a defensible reading - but it is a
   reading. **Post this on the Devpost forum today.**
2. **Run `mcp-grafana` in-process / as a sidecar.** Only possible because of cut 1 - Cloud Run
   can spawn subprocesses. Deletes the entire remote-MCP-auth problem. *~0.5 day.*
3. **Three fixed evaluation fixtures, not live fault roulette.** Roulette is an evaluation
   platform (scenario generation, reset determinism, oracle labels, aggregation), not "faults as
   data" - and it makes the video nondeterministic. *~1 day.*
4. **Approval lives outside ADK.** No native pause/resume. *~0.5 day.*
5. **Drop Prometheus.** Push metrics from a scale-to-zero service needs remote-write or Alloy,
   and Loki + Tempo *is* the narrative. *~0.3 day.*

**Do not cut:** the single-screen control room (Design is 25% and the cheapest criterion to
win), the black-policy correction, the approval boundary, or the refusal validator.

---

## Day-1 decisions (unrecoverable later)

| Decision | Note |
|---|---|
| **GCP region** | Firestore location is permanent; staging bucket must match |
| **ADC isolation** | `gcloud config configurations` does **not** isolate ADC. Use `CLOUDSDK_CONFIG` + a guard script that aborts unless account+project match. Machine is currently authed as `kloudfirst@gmail.com` / `nexkard-webapp-prod` - a **production project** |
| **Pin the ADK version** | Then test `output_schema`+tools *against that pin*. ADK main now has a `set_model_response` sample making them coexist, so the blocker is version-dependent, not universal |
| **Billing + quota** | New project = low default Vertex quotas. Request increases in the first 90 minutes |
| **Grafana Cloud tokens** | *Stack service-account* token against the stack URL - not a cloud access policy token. Getting this wrong looks like a network fault |

---

## Verified facts

- **`mcp-grafana` supports `stdio`, `sse`, and `streamable-http`** - remote hosting is possible;
  we choose in-process for simplicity.
- **Grafana Cloud free tier includes IRM**, capped at 3 active IRM users (we need 1).
  Limits: 10k series, 50GB logs, 50GB traces, **14-day retention**.
- **`get_panel_image` requires the separate Grafana Image Renderer** → PNG panels stay cut.
- **`otel-lgtm` datasource UIDs are `prometheus`/`loki`/`tempo`; Grafana Cloud auto-generates
  stack-specific UIDs.** The cutover is **not** an env-var swap - it needs an explicit UID
  remap. → We target **Grafana Cloud from day 3** and keep local only as an offline fallback.
- **Agent Engine bills active runtime only** (~$0.086/vCPU-hr) - moot given cut 1.
- **14-day retention vs Sept 23–Oct 7 judging:** telemetry recorded ~Sept 7 is **gone** before
  judges look. No "live dashboard" links in the submission. The video and repo are the artifacts.

---

## Architecture

### Topology

```
Browser
  → Cloud Run control plane (owns SSE, state, approval)
      → ADK agent (in-process) → mcp-grafana (sidecar) → Grafana Cloud read APIs
  ← immutable repair proposal
Browser approves (separate request)
  → control plane revalidates proposal hash + asset generation
      → ffmpeg worker (IAM-authenticated) → NEW GCS object
          → post-repair validation + telemetry
```

**The agent has no repair authority at all.** Repair authority is IAM on the worker, not a
Grafana write token. (Rev 2's "narrow write token" conflated two different things.)

### Investigation - 2 LLM calls, not 4

| Phase | How | Why |
|---|---|---|
| BASELINE | **Deterministic lookup** | "Fetch ingest QC for asset X" needs no model |
| DIVERGENCE | **Deterministic lookup** | "Which stage first out of spec" is a comparison |
| ACTOR | **LLM** + parameterised TraceQL | Genuine reasoning over the trace |
| CAUSE | **LLM** + parameterised LogQL | Genuine reasoning over preset logs |

This is honest (deterministic stages documented as deterministic), halves latency, and resolves
the reviewer tension: a judge greps for *"is the query derived or static?"* - here the LLM
phases supply **parameters derived from prior results** into vetted templates, and the
controller records exactly which query ran.

### Evidence provenance - the fix that matters most

**The controller binds `stage`, `query_used`, `raw_result_ref`, evidence ID, run ID, hash and
timestamps - server-side.** The model supplies only `finding`, `supports`, and prior evidence
IDs. Without this, the model can emit a perfectly valid evidence object citing a query it never
ran, and the whole integrity story is theatre.

`record_evidence` **catches `ValidationError` itself** and returns `{"ok": false, "error": ...}`.
A raised exception can abort the invocation rather than becoming a retryable response.

**The controller - not the model - verifies exactly one accepted evidence record per phase**,
and starts a bounded retry if not. **Plain Python, not `LoopAgent`** (deprecated in ADK main in
favour of Workflow, and it gives iteration count, not timeout or terminal escalation).

Restrict the toolset to **4–6 tools**. `mcp-grafana` exposes ~30, and tool-selection confusion at
that count is a measurable failure mode.

**Honest limit to state in the README:** schema validity proves *shape*, not *entailment*.
"A supporting step exists" ≠ "it supports the claim."

### Causation, honestly

"Preset v7 appeared at 14:02 and the next asset failed" is **correlation**. Either show the
**preset diff** that changes channel mapping, or phrase the conclusion as *"most likely
introducing configuration"* with calibrated confidence. Overclaiming here is exactly what a
domain judge would catch.

### Hero fault

Normalize applies `loudnorm` correctly. **Package** remaps audio channels via preset
`pkg_h264_v7`, so *delivered* loudness diverges from mastered. Defect at stage 3, cause a preset
change at stage 3. Attribution to **preset version, not worker**.

### Domain corrections (rev 1 was wrong)

Black is a **policy**, not a boolean - deliverables mandate head black, bars/tone, slate, 2-pop,
break black. Keep it to *explicitly scheduled segments plus precomputed telemetry*; full
content-aware detection is its own subsystem. Name a specific standard (EBU R128 −23 LUFS) and
ship a second profile (ATSC A/85 −24 LKFS) only if every rule is genuinely data-driven. Real
cost is cycle time and a lost slot, not automatic penalties. Say "three stages standing in for
a nine-stage Vantage workflow."

---

## Schedule (11 working days: Aug 29 – Sep 8)

**D1 Aug 29** - Decisions above. Repo + Apache-2.0. py3.12 venv. GCP project, billing, quota
requests. Grafana Cloud + stack token. **Forum post: 7.B dev-tools + Agent Builder/Cloud Run
compliance.** Pin ADK; test `output_schema`+tools against the pin.

**D2 Aug 30 - THE GATE.** Acceptance is strict:
> deployed browser starts a real run → Cloud Run → ADK agent → in-process mcp-grafana →
> Grafana Cloud → browser receives an event containing **a recognisable value from that real
> query**. No tunnel, no copied token, no mock, no manual console step. Run **cold once and warm
> once**.

**D3 Aug 31** - Three pipeline stages, real ffmpeg. Telemetry **to Grafana Cloud**. **Build the
local run harness** (20-second iteration loop) - mandatory and previously unbudgeted.

**D4 Sep 1** - GCS worker does one real re-encode. **Hand-solve the fault in Grafana Cloud**
before any agent code. Second hard gate: if a human can't, the agent can't.

**D5–6 Sep 2–3** - Agent: evidence contract + immutable ledger + tests; ACTOR/CAUSE phases;
controller-side completion and bounded retry; citation validator **with a test asserting an
uncited conclusion is rejected**; allowlist executor; approval flow; latency budget.
*(Reviews put this at 3–4 days. It is 2. This is the known debt - cuts 1, 2 and 4 are what pay
for it.)*

**D7 Sep 4** - Single-screen control room against the real path. **Record a complete, ugly,
unedited 3-minute run today.** Treat it as shippable. Every later day only improves it.

**D8 Sep 5** - Three fixtures × 5 runs = 15 runs. README results table with **separate
denominators** (fault attribution / correct source rejection / correct no-action). Publish raw
k/n including failures.

**D9 Sep 6** - Recovery and evaluation only. **No new product surface.** Half-day genuinely off,
laptop closed.

**D10 Sep 7** - Freeze. Clean-checkout deploy rehearsal. Hard 11 PM IST stop.

**D11 Sep 8** - Final recording + edit. README, ARCHITECTURE.md, diagram, Devpost writeup.
Hard 11 PM IST stop.

**Sep 9** - Submit by 6 PM IST. Portal trouble insurance only.

---

## The video

**First 20 seconds:** a video frame, a red **BLOCKED**, one wrong number. No architecture
diagram, no terminal scrolling logs, no Grafana dashboard, no "leveraging the power of."

**Climax - the refusal.** ⚠️ Do **not** rely on the model misbehaving on camera. **Inject a
deterministic adversarial candidate through the same production validator** so the rejection is
reproducible. Fifteen seconds, and it is the entire differentiation.

**Hero image:** the Tempo trace waterfall of the asset - three spans, QC measurement on each,
divergence highlighted. No other submission in this track can produce it.

**Persistent panel:** "what the model cannot do" - the three allowlist entries, visible
throughout.

---

## Landmines (all previously unbudgeted)

- **IAM: 6–10 hrs**, paid in 20–40 min interruptions every day
- **OTel *logs* → Loki from Python: 5–8 hrs**, least mature part of the ecosystem. `trace_id`
  must be in the Loki line or correlation doesn't exist
- **`-movflags +faststart` on every ffmpeg output** or the video won't seek in a browser and the
  demo *looks broken on camera*
- **Tempo trace lag is 30s–2min** → poll ceiling 90–120s, not 30s
- **Per-signal ingestion readiness:** one `pipeline.completed` watermark does **not** prove logs
  and traces are both queryable (independent exporters). Poll for a **telemetry manifest** -
  expected span IDs/counts and per-signal markers
- **SSE: `EventSource` cannot POST** → streaming `fetch` or a two-step run-ID design. Cloud Run
  300s timeout, response buffering, scale-to-zero killing in-flight streams
- **GCS signed URLs from Cloud Run** need IAM SignBlob + `serviceAccountTokenCreator`
- **Cloud Run `/tmp` is tmpfs and counts against memory** → 30–60s source clip only
- **Dependency conflicts 4–8 hrs**: `google-adk` + `opentelemetry-*` + `google-cloud-*` pin
  protobuf/grpcio against each other
- **`preset_version` as an OTel resource attribute *and* a low-cardinality Loki label.** Keep
  `job_id` out of labels - put it in the line. This is the payoff of the demo and the easiest
  thing to forget
- **Video: 12–16 hrs.** README to judge-grade: 5–8. Devpost writeup: 2–3. Diagram: 2–4

---

## Verification

**D2:** the strict gate above, cold and warm.
**D4:** hand-solve in Grafana Cloud including ingestion delay.
**D6:** uncited conclusion rejected (test); allowlist refuses an out-of-range parameter;
adversarial candidate rejected by the production validator.
**D7:** a complete ugly recording exists.
**D8:** 15 runs, results table with honest failures.
**D10:** clean-checkout deploy works.

**Acceptance:** run → BLOCKED with measured vs expected → deterministic BASELINE/DIVERGENCE →
LLM ACTOR/CAUSE with derived parameters → structured claims citing controller-bound evidence →
one visible refusal → engineer approves → worker re-encodes to a new GCS generation →
revalidated PASS → annotation + IRM incident in Grafana Cloud → under 3 minutes.

**Compliance:** real `from google.adk.agents import ...`; real `POST /api/annotations`, no mock;
Gemini via Vertex AI; **Agent Builder question answered on the forum**; no non-Google AI
framework in dependencies; Apache-2.0; public repo; hosted URL; ≤3 min public video.
