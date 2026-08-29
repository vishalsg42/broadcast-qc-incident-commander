# Decision log

Why this project is shaped the way it is. Each entry records the decision, what
drove it, and what it costs — including the ones that were wrong first time.

Most of these came out of four independent adversarial reviews of the plan (two
model reviews, a simulated hackathon judge, and a delivery-risk review). Several
overturned earlier decisions; those reversals are kept rather than tidied away,
because the reasoning is the useful part.

---

## D1 — Target Agentic Cinema, not All Things Agentic

**Context.** Two Google-run hackathons were open. All Things Agentic closed
Aug 31 with a $180k pool; Agentic Cinema closes **Sept 9** with $75k across five
identical per-track pools.

**Decision.** Agentic Cinema.

**Why.** Twelve days versus three, from an empty repo. Five separate tracks with
three places each means 15 payouts against a field pre-filtered by a mandatory
partner integration, versus one open-domain contest that will be flooded.
Lower ceiling ($7.5k vs $50k), better odds.

---

## D2 — Grafana track

**Context.** Five tracks, identical prizes: IBM, Grafana, Parallel, ClickHouse,
Replit.

**Decision.** Grafana.

**Why not IBM.** The track *mandates* IBM Bob, which is a build-time coding
assistant, not a runtime integration. It gives no technical moat and nothing that
can be shown on camera. The forum also showed broken access and one-hour trials.
An initial guess that "no blueprint means a thin field" was wrong and reversed:
the lowest barrier to entry attracts the *most* entrants, and with no moat you
compete on idea alone against the largest pool.

**Why not ClickHouse.** Its build session was "The Agent's Memory" — chat-with-data
is the blueprint, and "agent writes SQL over a big table" is the single most
common agent demo in existence.

**Cost.** Grafana's integration is real work. That is also the point: it filters
the field.

---

## D3 — Do not build the workshop project

**Context.** Grafana ran an official session, *"Premiere Night — Build an
Observability Agent with Grafana Cloud MCP and Google ADK"*, demonstrating
incident investigation in a **simulated streaming platform**. The recording is
posted.

**Decision.** Deliberately depart from it: treat a **media artefact**, not a
service, as the incident subject.

**Why.** A large share of that track will submit variants of the worked example.
The novel move is a Tempo trace whose spans are the life of one video file, with
QC measurements attached — observability pointed at media essence rather than
infrastructure health.

**Tension acknowledged.** Familiar *mechanics* (telemetry → violation →
investigate → annotate) with an unfamiliar *domain* is the intended position:
legible to a judge in 30 seconds, but not the fortieth copy.

---

## D4 — Ground truth is the whole point

**Decision.** ffmpeg produces a real number; a YAML profile says whether it is in
spec; after repair the **same code** says it is. The conclusion is falsifiable.

**Why.** Every other submission in this space investigates a fabricated outage and
produces a plausible narrative nobody can check. This is the one property that
lets a judge tell whether the agent was *right* rather than merely fluent.

**Consequence.** `pipeline/policy.py` contains zero AI and is called twice — once
to block, once to clear.

---

## D5 — Black is a policy, not a boolean *(correction)*

**Was wrong.** The first design failed on any detected black frame.

**Why that was wrong.** Deliverables *mandate* black: head black (~10s), bars and
tone, slate, 2-pop, black between programme parts, ad-break black. A boolean check
would reject nearly every legitimate master in existence — a domain reviewer would
dismiss the whole QC layer in ten seconds.

**Decision.** Black policy with permitted regions (required head, optional tail,
resolved relative to duration) plus a `max_contiguous_black_s` rule inside the
programme body.

**Upside.** "Legal black vs illegal black" is genuinely non-trivial policy, and it
showcases why a deterministic profile engine beats a model eyeballing frames.

**Scope limit.** Explicitly scheduled segments only. Content-aware black
classification is a separate subsystem and is out of scope.

---

## D6 — Loudness standards, stated correctly *(correction)*

**Was wrong.** Earlier drafts claimed loudness compliance is "legally mandated in
the US and EU" and treated EBU −23 LUFS, ATSC −24 LKFS and Netflix −27 LKFS as
interchangeable numbers.

**Corrected.**
- **EBU R128** is an EBU *recommendation*, widely adopted by European broadcasters
  and national regimes — not EU law.
- **The CALM Act** governs the loudness of **commercial advertisements**, via FCC
  rules incorporating ATSC A/85. Not blanket programme-delivery law.
- **Netflix ≈ −27 LKFS is dialogue-gated** — a different measurement that
  `ebur128` does not report. It needs its own profile, not a changed number.

---

## D7 — The hero fault: defect and cause in different stages *(revision)*

**Was.** A misconfigured normaliser producing wrong loudness.

**Why that was weak.** Loudness is the *least* painful failure in the delivery
spec: measured in one pass, fixed by a second. Everyone already knows the answer
is the normalise stage — which contradicts the project's own premise that you
cannot tell which stage broke it.

**Now.** Normalize applies `loudnorm` **correctly**. The **package** stage then
remaps audio channels via preset `pkg_h264_v7` (`pan=stereo|c0=c0+c1|c1=c0+c1`),
summing an already-stereo bed into both channels. Measured: −23.0 → −22.8 →
**−16.8 LUFS**.

**And attribution targets a preset version, not a worker.** Real facilities do not
hunt for a broken machine; they hunt for which transcode preset changed and when.
"Preset `pkg_h264_v7`, changed 14:02" is far more convincing than "worker-3".

---

## D8 — Faults are configuration, not code

**Decision.** Faults live in `pipeline/presets.yaml` as versioned presets with
`changed_at` timestamps. Injection is `overrides={"package": "pkg_h264_v7"}`.

**Why.** A judge greps the failure path first. `if asset_id == "demo_001"` turns
the whole project into a diorama.

---

## D9 — The controller binds evidence provenance *(the most important fix)*

**Was.** The model supplied `query_used` and `raw_result_ref` on each evidence
record.

**The hole.** A model can then emit a perfectly schema-valid evidence object
**citing a query it never ran**, and the entire integrity story is theatre.

**Now.** `EvidenceLedger.observe()` is called by the controller *after* the query
executes, binding `phase`, `query_used`, `query_hash`, `raw_result_ref`,
`step_id`, `run_id` and timestamps. The model calls `record_interpretation()` and
supplies only `finding` and `supports`.

**Honest limit, stated in the code and the README.** Schema validity proves
*shape*, not *entailment*. The validator catches uncited, mis-cited and fabricated
references. It does not verify that the reasoning is sound.

---

## D10 — Structure from tool arguments, not `output_schema`

**Context.** ADK documents that `output_schema` with `tools` in the same request
is model-dependent, and its samples carry *"NO tools parameter here — using
output_schema prevents tool use"*. Verified against ADK 2.8.0: it **constructs**
without error, so the blocker is not at construction, but relying on it would be
building the core design on a version- and model-dependent behaviour.

**Decision.** Get structure from **typed tool arguments** instead. The
`record_evidence` signature *is* the schema; function-calling enforces the shape;
Pydantic validates inside the tool.

**Corollary.** A tool must **never raise** — ADK turns an exception inside a tool
into an aborted invocation rather than a retryable response. See
`safe_record_interpretation`, which returns `{"ok": False, "error": ...}`.

**Corollary.** `LoopAgent` is deprecated in ADK 2.8.0 in favour of Workflow, and
supplies an iteration count rather than a timeout or terminal escalation. Bounded
retry is plain Python.

---

## D11 — Two LLM calls, not four

**Decision.** `BASELINE` and `DIVERGENCE` are deterministic lookups. `ACTOR` and
`CAUSE` use the model.

**Why.** "Fetch ingest QC for asset X" and "which stage first went out of spec"
need no model — they are a lookup and a comparison. Four sequential model calls
plus telemetry ingestion latency would also make a three-minute demo glacial.

**Answering the obvious objection.** A judge greps for *"is the query derived from
the previous result, or a static string?"* The model phases supply **parameters
derived from prior results** into vetted query templates, and the controller
records exactly which query ran. The deterministic phases are documented as
deterministic rather than dressed up.

---

## D12 — The agent has no execution authority

**Decision.** Three allowlisted actions, defined in the delivery profile.
The agent names an `action_id` and supplies typed parameters;
`pipeline/remediation.py` re-validates the request before running anything, and
repairs always write a **new** artefact rather than overwriting the input.

**Also.** A human approves before execution. No facility lets software mutate a
master unsupervised, and showing the approval boundary *raises* the impact claim
rather than weakening it.

**Note.** Repair authority is IAM on the worker — not a Grafana write token. An
earlier draft conflated the two.

---

## D13 — Scope cuts to fit eleven days

Four reviews independently estimated the original plan at ~175 hours against
~85–95 productive hours. Cuts made:

| Cut | Saves |
|---|---|
| Host ADK on Cloud Run rather than Agent Engine (keep agent logic host-agnostic) | ~1.5 d |
| Run `mcp-grafana` in-process — only possible because of the above | ~0.5 d |
| Three fixed evaluation fixtures instead of live "fault roulette" | ~1 d |
| Approval flow outside ADK — no native pause/resume | ~0.5 d |
| Drop Prometheus; Loki + Tempo is the narrative | ~0.3 d |

**Not cut, deliberately:** the single-screen control room (Design is 25% of the
score and the cheapest criterion to win), the black-policy correction, the
approval boundary, the refusal validator.

**Open compliance question.** The rules require "powered by Gemini and Google
Cloud Agent Builder". ADK ships under Agent Builder, so ADK-on-Cloud-Run is a
defensible reading — but it is a reading, and it is unresolved.

---

## D14 — Rule 7.B: AI tooling restriction

**Finding.** Three forum threads carry official organizer answers stating that
Section 7.B covers the **entire development workflow**, not only runtime — that
only Gemini CLI, Gemini Code Assist and Google's Antigravity suite are permitted
for coding assistance, that this extends to *"planning, troubleshooting, test
scaffolding, or project-management guidance"*, and that even Google Stitch does
not qualify because it is not part of the Google Cloud AI tool suite.

**Consequence.** Development tooling for this submission must come from Google's
approved list, and any prior AI assistance should be disclosed. Recorded here so
the provenance question is answered in the open rather than left implicit.

---

## D15 — Reproducible synthetic fixtures

**Decision.** Generate fixtures with ffmpeg rather than sourcing footage.

**Why.** The *measurements* are real — ffmpeg genuinely decodes these files — but
the inputs are byte-reproducible, so evaluation runs are comparable and the demo
does not depend on a stock clip's licensing.

**Trap paid for.** The ffmpeg concat **demuxer** with `-c copy` produced an 86s
file from 45s of input (AAC edit-list accumulation across segments). Use the
concat **filter** in a single pass. And every output needs `-movflags +faststart`,
or a browser cannot seek and a working demo looks broken on camera.

---

## D16 — Retrieval is deterministic; only interpretation is model work

**Decision.** All four investigation phases retrieve their evidence with
controller-written queries. The model is handed one result at a time and asked
one fixed question about it.

**Why.** "Fetch the ingest QC line for this run" needs no model, and dressing it
up as agentic reasoning would be theatre. What genuinely needs judgement is what
each result *means*, whether it supports or refutes the emerging explanation, and
what to propose — and that is exactly the surface the model gets.

**The objection this has to survive.** A judge greps for *"is the query actually
derived from the previous result, or a static string?"* Here the queries are
templates parameterised by prior results — `gather_actor(failing_stage)` cannot
be written until DIVERGENCE returns a stage — and the controller records the
exact query that ran. The determinism is documented rather than disguised.

---

## D17 — `Reasoner` protocol with two implementations

**Decision.** `GeminiReasoner` (ADK + Vertex) and `ScriptedReasoner`
(deterministic), behind one interface. `demo.py --reasoner scripted|gemini`.

**Why.** Three reasons, in order. The loop stays testable with no cloud
dependency and no token burn. Demo takes are reproducible, so a recording does
not depend on the model phrasing things well on the fourth attempt. And the swap
is one flag, which means the model can be wired in the moment credentials exist
rather than being a blocking dependency for everything downstream.

**Not a mock.** `ScriptedReasoner` goes through `safe_record_interpretation`
exactly as the real one does, so provenance binding, validation and the refusal
path are all genuinely exercised.

---

## D18 — Controller-side retry, because ADK guarantees order not action

**Decision.** If the model answers without calling `record_evidence`, the
controller re-prompts up to `max_attempts` and then raises.

**Why.** `SequentialAgent` guarantees that sub-agents run in order. It does not
guarantee that a tool was called, that the expected query was used, or that
evidence exists. Without a controller-side check, a phase can silently complete
having recorded nothing, and the citation validator would then reject a
conclusion for a reason that looks like a model failure but is actually a
plumbing failure.

---

## D19 — Three test tiers

**Decision.** Pure unit (~3s), `media` (shells out to ffmpeg, ~2m), `integration`
(needs live Loki/Tempo, ~4m).

**Why.** The reviews were emphatic that a fast iteration loop is mandatory and
unbudgeted. Real ffmpeg fixtures cost 15–42s of setup each, which is fine for a
pre-commit run and fatal for a per-save one.

**Trap worth recording.** Two suites running concurrently against the same Docker
stack and ffmpeg look exactly like a hang. Kill stray runs before diagnosing a
"regression".


---

## D20 — `output_schema` + tools works here, and we still do not use it

**Measured, not assumed.** On `gemini-2.5-flash` via Vertex AI with ADK 2.8.0,
an `LlmAgent` carrying both `tools` and `output_schema` calls the tool *and*
honours the schema. That contradicts the general documentation warning, which is
why the probe exists rather than a guess.

**The design does not change.** Structure was never the requirement — provenance
was. `record_evidence` stays because it keeps `phase`, `query_used`,
`query_hash` and `raw_result_ref` under the controller's control. An
`output_schema` would let the model emit a well-shaped object describing a query
it never ran.

**Also measured:** ADK flags the generated schema as experimental
(`JSON_SCHEMA_FOR_FUNC_DECL`) and exposes it as `parameters_json_schema`, not
the older `parameters` field. `tests/test_adk_contract.py` pins the model-facing
surface so an ADK bump cannot widen it silently.

---

## D21 — Gemini's prose is thinner than the scripted stand-in; the conclusion is not

Running the same fixture on both reasoners, Gemini's ACTOR finding read *"The
failing package stage ran with preset version 7"* where the scripted version
names `pkg_h264_v7` and its `changed_at`.

**The conclusion was identical either way**, because `build_conclusion` draws on
the controller-bound phase summary rather than the model's sentence. This is the
provenance design absorbing a weaker model output without the attribution
degrading — the intended behaviour, observed rather than argued.

A sharper ACTOR prompt would improve the on-screen narration. It would not
change what the system concludes, which is the point.


---

## D22 — The Grafana Cloud cutover, and what it actually cost

The earlier claim that moving from local to Cloud is "an env-var swap" was
wrong three times over. Recording what it really took, because every one of
these failed *silently* — an empty query result is indistinguishable from
"that run does not exist."

**1. Datasource UIDs are stack-specific, and there are several per type.**
This stack ships three Loki datasources: `-logs`, `-alert-state-history`, and
`-usage-insights`. Taking the first match queried usage-insights and returned
nothing. `scripts/check_grafana.py` now prints every candidate and marks its
choice.

**2. `OTEL_EXPORTER_OTLP_HEADERS` must be quoted in `.env`.** The value
contains a space, so an unquoted line is truncated by the shell at `Basic`.
Grafana answers `401 no credentials provided`, which reads as a bad token
rather than a quoting bug.

**3. `GrafanaConfig(url=...)` silently drops the token and the UIDs.** Three
entrypoints constructed it directly, and `demo.py` compounded it with a
`--grafana` flag defaulting to localhost — so telemetry shipped to Cloud while
queries went to the local stack. `from_env()` is now the only construction
path, and its docstring says why.

**Also:** Cloud ingestion routinely exceeds 90s, so `ingest_timeout_s` adapts —
90s local, 300s Cloud.

**And a false green of my own making.** The credential checker verified the
OTLP header was *shaped* correctly and never that it *worked*. It now POSTs to
the gateway. A check that cannot fail is not a check.

---

## D23 — Grafana IRM needed provisioning, not code

`IncidentsService.QueryIncidentPreviews` returned 200 while `CreateIncident`
returned 500 with a foreign-key violation on `Counters.orgID`. Plugin
installed, token permitted, request well formed — Grafana's own IRM database
simply had no counter row for the org, so incident IDs could not be allocated.

Nothing on this side could repair server-side state. Opening **Incidents** once
in the Grafana UI provisioned it, and incident creation has worked since.

The lesson kept in code: a raw foreign-key error surfaced verbatim reads like
our bug. `create_incident` now recognises that signature and says what is
actually wrong and how to fix it.


---

## D24 — Deploying to Cloud Run, and the four bugs it exposed

Containerising found four defects that local development could not, because
each depends on an environment property a laptop does not have.

**1. A missing runtime dependency.** `opentelemetry-exporter-otlp-proto-http`
had been pip-installed after `requirements-lock.txt` was frozen and never
re-frozen. Worked locally forever; `ModuleNotFoundError` in any clean
environment.

**2. A hardcoded output path.** `OUT_DIR` pointed at `ROOT/out`, which cannot be
written on Cloud Run's read-only filesystem. Now `QCIC_OUT_DIR`, defaulting to
`/tmp` in the image.

**3. An unbounded SSE stream.** The generator looped until an `end` event, so an
abandoned connection held a FastAPI threadpool thread forever. A handful of
closed tabs exhausted the pool and the service stopped answering entirely — a
self-inflicted denial of service on a public URL. Now hard-capped.

**4. Fixtures never reached the image.** `gcloud run deploy --source .` falls
back to `.gitignore` when no `.gcloudignore` is present, and `.gitignore`
excludes `media/*.mp4`. The container deployed cleanly and failed at the first
ffmpeg call with "source not found". Fixed by generating fixtures during the
image build — reproducible by construction, and impossible to omit.

**Also corrected in flight:** `--max-instances 2` was wrong. Run state and the
approval handshake are in process memory, so a second instance could receive an
approval for a run it does not know. Pinned to 1.

**And a fail-fast.** With telemetry unconfigured the service used to block
correctly, then wait the full 300s ingestion ceiling and report "0 lines" —
which reads as a Grafana outage rather than a missing environment variable. It
now refuses to start the run and names the variable.

Verified in production: real Gemini via Vertex AI, real Grafana Cloud, real
ffmpeg. Telemetry queryable in **1.0s** thanks to the boot warm-up, four
refusals, approval, repair, re-validation, annotation and IRM incident.

## D25 — The refusal had to be reachable, not merely implemented

`policy.py` could always answer `UNMEASURABLE`, and `tests/test_profiles.py`
proved it. But `PROFILE_PATH` was hardcoded to `ebu_r128.yaml`, `StartRequest`
had no `profile_id`, and the UI never asked. **The best idea in the project was
unreachable from the product** — an answer no caller can obtain is
indistinguishable from one that does not exist.

The profile is now selected per run: a registry in `policy.py`, `profile_id` on
`POST /api/runs`, all three profiles listed by `GET /api/profile`, and a
selector in the control room. One asset, one measurement, three correct and
different answers — BLOCKED under R128, BLOCKED under A/85, UNMEASURABLE under
Netflix.

The refusal terminates the run deliberately. There is no investigation and no
proposed repair, because attributing a defect presupposes a verdict that was
never reached. Netflix's allowlist contains `escalate_to_human` and nothing
else, so the data says the same thing the code does.

**A submission-blocking bug fell out of this.** The deployed UI could not start
a run at all: `DEMO_TOKEN` is set in production, `require_token` reads a `token`
query parameter, and `useRun` posted without one — every click of Run delivery
returned 403 and the page looked dead. It went unnoticed because every previous
production run was driven from a script, never from the hosted page. The hook
now forwards the token from the address bar, and a 403 says the link is missing
its token rather than showing a bare status code.

## D26 — Change provenance: the question after the version

Attribution to a preset version is this project's differentiator, but the domain
review was blunt about where it falls short:

> "The very first question in the room after 'preset v7, changed 14:02' is who
> changed it, and was it signed off. A delivery preset change is a controlled
> change."

So `presets.yaml` now carries `changed_by`, `change_ticket` and `approved_by`
alongside `changed_at`. Three strings, no new subsystem.

**They ride the trace span, and the ACTOR phase reads them back off the span —
not off the preset file.** The file says what a preset is *now*; the span says
what actually *ran*. If a preset were edited after a run, only the span would
still be right, and provenance read from the file would quietly describe a
different change than the one under investigation.

Three deliberate choices:

**Absent fields are reported, not hidden.** A preset with no recorded approval
renders "no approval recorded" and claims "with no approval recorded". An
uncontrolled change to a delivery preset is a finding; a blank row is not.

**Provenance is a separate claim from root cause.** Who changed it explains who
to talk to; it never explains why the asset failed. Fusing them would let an
audit trail masquerade as a causal explanation.

**The hero preset was properly approved and still shipped a defect.** `CHG-4471`
went through change control. That is the honest case and the more useful one:
approval is an audit trail, not a technical review, which is exactly why
attribution must be measured from telemetry rather than inferred from process.

**A fixture bug this surfaced:** `pkg_h264_v7` was timestamped `14:02Z` while the
run executed at `09:06Z`, so `days_since_change` came back **negative** — a
preset changed in the future. Moved to `04:02Z`. Note that `RECENT_CHANGE_DAYS`
is 7, so a run more than a week after that date correctly reports "preset
SELECTION rather than a preset change" instead — the second branch, working as
designed.

## D27 — Restoring autonomy without giving up a boundary

The four-phase investigation was not an agent. `scripts/evaluate.py --reasoner
scripted` scored 3/3 with **no AI at all**, and BASELINE and DIVERGENCE handed
the model a dict already containing `source_in_spec` and `first_failing_stage` —
a frontier model paid to rephrase a boolean. In an agentic competition that is
indefensible, and three independent reviews said so.

The model now plans its own investigation. Its Grafana surface is the **official
Grafana MCP server** — the managed protocol adapter the partner track describes —
filtered to six read-only tools out of the 74 it exposes.

**What made this safe was not the prompt.** Three refusals replace what a fixed
sequence used to guarantee structurally:

- **An evidence floor.** One query and an assertion is not an investigation.
  Set at two kinds, not three: a higher floor is unreachable for a fault that
  arrived with the source, where there is no preset to look up and no earlier
  version to test, so the agent could gather forever and never clear it.
- **A claim-source table.** `validate_conclusion` proves a cited step *exists*;
  it cannot prove the step *supports* the claim. Moving citation authorship from
  `build_conclusion()` to the model made that gap matter, so each claim type is
  bound to the evidence that can bear on it. The one that earns its keep is
  `EXPERIMENT`: without it, "I tested it and measured +6.1 LU" citing a preset
  lookup passes every structural check with no experiment ever run.
- **A requirement to test before blaming.** This project's own standard, applied
  to the agent. Enforced in code rather than requested in the prompt, because
  "please test your hypothesis" is a request and this is a requirement.

All three fired against the real model, and it corrected itself each time.

**What the reviews caught before this shipped.** The plan's headline claim was
inoperative: `observe()` derived step ids from `len(_steps)`, which only grows on
interpretation, so consecutive observations all minted `step-01` and overwrote
each other's raw results. The plan also asserted evidence binding got *stronger*;
it gets weaker in exactly one direction, and `evidence.py` already said so.

**The honest limit is unchanged.** Nothing here verifies reasoning. A cited step
is real and of the right kind; whether it entails the claim is not checked, and
no wording in this repository should suggest otherwise.

`--reasoner scripted` is untouched and remains the reproducible path.
