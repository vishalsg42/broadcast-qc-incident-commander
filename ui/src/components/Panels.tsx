"use client"

import { StageChart } from "./StageChart"
import { STAGE_PLAIN, TOOL_PLAIN } from "@/lib/types"
import type {
  AgentCall,
  AgentFinishedEvent,
  ApprovalEvent,
  ConclusionEvent,
  ExperimentEvent,
  RefusalEvent,
  RepairedEvent,
  StageEvent,
  TraceEvent,
  UnmeasurableEvent,
  WriteBackEvent,
} from "@/lib/types"

export function SignalPath({
  stages,
  activeStage,
  target,
  tolerance,
  withheld,
}: {
  stages: StageEvent[]
  activeStage: string | null
  target: number
  tolerance: number
  withheld: boolean
}) {
  const order = ["ingest", "normalize", "package"]
  return (
    <div className="panel">
      <StageChart
        stages={stages}
        target={target}
        tolerance={tolerance}
        withheld={withheld}
      />
      {/* Always three slots. They fill in as each stage finishes, so the
          reader watches the signal path build rather than seeing it appear
          complete after a silent minute. */}
      <div className="grid grid-cols-3 divide-x divide-rule">
        {order.map((name) => {
          const done = stages.find((s) => s.stage === name)
          const running = activeStage === name

          if (!done) {
            return (
              <div key={name} className="px-4 py-3">
                <div className="legend">
                  {name} <span className="text-legend">· {STAGE_PLAIN[name]}</span>
                </div>
                <div
                  className={`meter mt-1 text-lg text-legend${running ? " working" : ""}`}
                >
                  {running ? "measuring" : "-"}
                </div>
                <div className="meter mt-1 text-[0.6875rem] text-legend">
                  {running ? "ffmpeg running" : "\u00a0"}
                </div>
              </div>
            )
          }

          const bad = done.verdict === "BLOCKED"
          // A stage that was never judged must not wear the in-spec marker.
          // Green would claim a pass the gate explicitly declined to give.
          const held = done.verdict === "UNMEASURABLE"
          const mark = held
            ? "var(--color-pending)"
            : bad
              ? "var(--color-blocked)"
              : "var(--color-inspec)"
          return (
            <div key={name} className="enter px-4 py-3">
              <div className="flex items-center justify-between">
                <span className="legend">
                  {name} <span className="text-legend">· {STAGE_PLAIN[name]}</span>
                </span>
                <span
                  aria-hidden
                  className="h-1.5 w-1.5"
                  style={{
                    background: mark,
                    // Hollow, not filled: measured, but not adjudicated.
                    boxShadow: held ? `inset 0 0 0 1px ${mark}` : undefined,
                    opacity: held ? 0.5 : 1,
                  }}
                />
              </div>
              <div
                className="meter mt-1 text-lg"
                style={{
                  color: bad
                    ? "var(--color-blocked)"
                    : held
                      ? "var(--color-read)"
                      : "var(--color-bright)",
                }}
              >
                {done.integrated_lufs.toFixed(1)}
              </div>
              <div className="meter mt-1 text-[0.6875rem] text-legend">
                {done.preset_id} v{done.preset_version}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function AttributionPanel({ trace }: { trace: TraceEvent | null }) {
  return (
    <div className="panel p-4">
      <span className="legend">What caused it</span>
      {trace ? (
        <div className="enter mt-2">
          <div className="meter text-lg text-pending">{trace.preset_id}</div>
          <dl className="mt-2 space-y-1 text-[0.8125rem]">
            <Row label="Version" value={`v${trace.preset_version}`} />
            <Row label="Changed" value={trace.preset_changed_at} />
            <Row label="Stage" value={trace.stage} />
            <Row label="Trace" value={trace.trace_id.slice(0, 16)} />
          </dl>

          {/*
            A delivery preset change is a controlled change, so the incident
            conversation goes here next: who changed it, under what ticket, and
            who signed it off. Absent fields say "not recorded" rather than
            being hidden - the gap is itself the finding.
          */}
          <div className="mt-3 border-t border-rule pt-3">
            <span className="legend">Who changed this setting</span>
            <dl className="mt-2 space-y-1 text-[0.8125rem]">
              <Row label="Changed by" value={trace.preset_changed_by ?? "not recorded"} />
              <Row label="Ticket" value={trace.preset_change_ticket ?? "not recorded"} />
              <Row
                label="Approved"
                value={trace.preset_approved_by ?? "no approval recorded"}
              />
            </dl>
          </div>
        </div>
      ) : (
        <p className="mt-2 text-sm text-legend">
          The setting that produced the defect appears here once the trace is read.
        </p>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="legend">{label}</dt>
      <dd className="meter text-read">{value}</dd>
    </div>
  )
}

/**
 * The constraint, visible the whole time rather than asserted at the end.
 * The agent can only ever name one of these three, with validated parameters.
 */
export function AllowlistPanel({
  allowlist,
  refusals,
}: {
  allowlist: string[]
  refusals: RefusalEvent[]
}) {
  return (
    <div className="panel">
      <div className="border-b border-rule px-4 py-2.5">
        <span className="legend">What the model may propose</span>
      </div>
      <ul className="divide-y divide-rule/60">
        {allowlist.map((action) => (
          <li key={action} className="meter px-4 py-2 text-[0.8125rem] text-read">
            {action}
          </li>
        ))}
      </ul>
      <div className="border-t border-rule px-4 py-2.5">
        <span className="legend">Everything else is refused</span>
      </div>
      {refusals.length > 0 && (
        <ul className="divide-y divide-rule/60">
          {refusals.map((r) => (
            <li key={r.name} className="enter px-4 py-2.5">
              <div className="flex items-baseline gap-2">
                <span
                  className="legend"
                  style={{ color: r.refused ? "var(--color-blocked)" : "var(--color-pending)" }}
                >
                  {r.refused ? "refused" : "accepted"}
                </span>
                <span className="meter text-[0.75rem] text-read">{r.name}</span>
              </div>
              <p className="mt-1 text-[0.75rem] leading-snug text-legend">{r.reason}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ConclusionPanel({ conclusion }: { conclusion: ConclusionEvent | null }) {
  if (!conclusion) return null
  return (
    <div className="panel">
      <div className="flex items-center justify-between border-b border-rule px-4 py-2.5">
        <span className="legend">Conclusion</span>
        <span
          className="legend"
          style={{
            color: conclusion.accepted ? "var(--color-inspec)" : "var(--color-blocked)",
          }}
        >
          {conclusion.accepted ? "validator accepted" : "validator rejected"}
        </span>
      </div>
      <ul className="divide-y divide-rule/60">
        {conclusion.claims.map((claim, i) => (
          <li key={i} className="enter px-4 py-3">
            <p className="text-sm text-read">{claim.claim_value}</p>
            <div className="mt-1.5 flex gap-3">
              <span className="legend">{claim.claim_type.toLowerCase().replace(/_/g, " ")}</span>
              <span className="meter text-[0.6875rem] text-legend">
                cites {claim.cites.join(", ")}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ProposalPanel({
  approval,
  onDecide,
  repaired,
  writeBack,
  escalation,
}: {
  approval: ApprovalEvent | null
  onDecide: (approved: boolean) => void
  repaired: RepairedEvent | null
  writeBack: WriteBackEvent | null
  escalation: string | null
}) {
  if (escalation) {
    return (
      <div className="panel border-l-2 p-4" style={{ borderLeftColor: "var(--color-pending)" }}>
        <span className="legend">Escalated - no repair proposed</span>
        <p className="mt-2 text-sm text-read">{escalation}</p>
      </div>
    )
  }

  if (approval) {
    return (
      <div
        className="panel enter border-l-2 p-4"
        style={{ borderLeftColor: "var(--color-pending)" }}
      >
        <span className="legend">Repair proposed - engineer approval required</span>
        <div className="meter mt-2 text-sm text-bright">
          {approval.action_id}({JSON.stringify(approval.params)})
        </div>
        <p className="mt-1.5 text-sm text-legend">{approval.rationale}</p>
        <div className="mt-4 flex gap-2">
          <button
            onClick={() => onDecide(true)}
            className="legend cursor-pointer bg-inspec px-4 py-2 text-ink transition-opacity hover:opacity-85"
          >
            Approve repair
          </button>
          <button
            onClick={() => onDecide(false)}
            className="legend cursor-pointer border border-rule px-4 py-2 text-read transition-colors hover:border-legend"
          >
            Reject
          </button>
        </div>
      </div>
    )
  }

  if (repaired) {
    return (
      <div
        className="panel enter border-l-2 p-4"
        style={{
          borderLeftColor: repaired.resolved ? "var(--color-inspec)" : "var(--color-blocked)",
        }}
      >
        <span className="legend">Re-validated by the same gate that blocked it</span>
        <div
          className="mt-2 text-lg font-semibold"
          style={{
            color: repaired.resolved ? "var(--color-inspec)" : "var(--color-blocked)",
          }}
        >
          {repaired.resolved ? "Delivery cleared" : "Still blocked"}
        </div>
        <p className="meter mt-1 text-[0.75rem] text-legend">{repaired.message}</p>
        {writeBack && (
          <p className="meter mt-2 text-[0.75rem] text-legend">
            Grafana: annotation {writeBack.annotation_ok ? "written" : "failed"}
            {" · "}
            incident {writeBack.incident_ok ? "opened" : "skipped"}
          </p>
        )}
      </div>
    )
  }

  return null
}

/**
 * The refusal to adjudicate.
 *
 * Amber rather than red on purpose: this is not a failed asset. The asset was
 * never judged, because the profile demands a measurement this probe cannot
 * make. Reporting that plainly is the alternative to a confident wrong verdict.
 */
export function UnmeasurablePanel({ event }: { event: UnmeasurableEvent | null }) {
  if (!event) return null
  return (
    <div
      className="panel enter border-l-2 p-4"
      style={{ borderLeftColor: "var(--color-pending)" }}
    >
      <div className="flex items-center justify-between">
        <span className="legend">Verdict withheld</span>
        <span
          className="text-sm font-bold tracking-[0.1em] uppercase"
          style={{ color: "var(--color-pending)" }}
        >
          Unmeasurable
        </span>
      </div>
      <p className="mt-3 text-[0.8125rem] leading-relaxed text-read">
        {event.plain_reason || event.reason}
      </p>
      {event.plain_reason && (
        <p className="mt-2 text-[0.75rem] leading-snug text-legend">{event.reason}</p>
      )}
      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        <dt className="legend">Profile</dt>
        <dd className="meter text-[0.75rem] text-read">{event.profile_name}</dd>
        <dt className="legend">Requires</dt>
        <dd className="meter text-[0.75rem] text-read">{event.requires}</dd>
      </dl>
      <p className="legend mt-3 leading-relaxed">
        Nothing is repaired. You cannot fix a file against a rule you cannot
        measure, so the system stops rather than guessing.
      </p>
    </div>
  )
}

/**
 * The experiment.
 *
 * Everything else on this screen is retrieval - the system reading what already
 * happened. This is the one place it ACTS: it re-runs the failing stage on the
 * same input with the preset that normally runs, and measures both. A control
 * that passes where the suspect blocks rules out the input and reproduces the
 * defect on demand.
 *
 * The delta is stated as what happened to THIS asset. It is a property of the
 * content, not of the preset - identical channels sum to +6 dB, decorrelated
 * stereo to about +3 - so the caveat is shown, not buried.
 */
export function ExperimentPanel({
  running,
  experiment,
}: {
  running: boolean
  experiment: ExperimentEvent | null
}) {
  if (!running && !experiment) return null

  if (running || !experiment) {
    return (
      <div className="panel px-4 py-3">
        <span className="legend">Testing the suspected cause</span>
        <p className="working mt-1 text-[0.8125rem] text-bright">
          re-running the stage with the previous setting to compare
        </p>
      </div>
    )
  }

  const arms = [
    {
      label: "Normal setting",
      id: experiment.control_preset_id,
      version: experiment.control_preset_version,
      lufs: experiment.control_lufs,
      verdict: experiment.control_verdict,
    },
    {
      label: "Suspected setting",
      id: experiment.suspect_preset_id,
      version: experiment.suspect_preset_version,
      lufs: experiment.suspect_lufs,
      verdict: experiment.suspect_verdict,
    },
  ]

  return (
    <div className="panel enter">
      <div className="flex items-baseline justify-between border-b border-rule px-4 py-2.5">
        <span className="legend">We tested it</span>
        <span
          className="legend"
          style={{
            color: experiment.reproduces_defect
              ? "var(--color-blocked)"
              : "var(--color-legend)",
          }}
        >
          {experiment.reproduces_defect
            ? "the fault was reproduced"
            : "the fault was not reproduced"}
        </span>
      </div>

      <p className="px-4 pt-3 text-[0.8125rem] text-read">
        Same file, same step, run twice - once with the setting that normally
        runs, once with the suspected one.
      </p>

      <div className="mt-3 grid grid-cols-2 divide-x divide-rule border-t border-rule">
        {arms.map((arm) => {
          const bad = arm.verdict === "BLOCKED"
          return (
            <div key={arm.id} className="px-4 py-3">
              <div className="legend">{arm.label}</div>
              <div
                className="meter mt-1 text-2xl"
                style={{
                  color: bad ? "var(--color-blocked)" : "var(--color-inspec)",
                }}
              >
                {arm.lufs.toFixed(1)}
              </div>
              <div className="meter mt-1 text-[0.6875rem] text-legend">
                {arm.id} v{arm.version}
              </div>
              <div
                className="legend mt-1"
                style={{
                  color: bad ? "var(--color-blocked)" : "var(--color-inspec)",
                }}
              >
                {bad ? "would be rejected" : "would be accepted"}
              </div>
            </div>
          )
        })}
      </div>

      <div className="border-t border-rule px-4 py-3">
        <div className="flex items-baseline gap-2">
          <span
            className="meter text-lg"
            style={{ color: "var(--color-pending)" }}
          >
            {experiment.delta_lu >= 0 ? "+" : ""}
            {experiment.delta_lu.toFixed(2)} LU
          </span>
          <span className="legend">difference caused by the setting</span>
        </div>
        <p className="legend mt-2 leading-relaxed">{experiment.caveat}</p>
      </div>
    </div>
  )
}

/**
 * What the agent chose to do.
 *
 * The difference between an agent and a script is not visible in a result - both
 * produce steps in a list. It is visible in the SEQUENCE: tools picked in an
 * order nobody fixed, a refusal, and the agent trying again differently.
 *
 * So the refusals are shown rather than hidden. "conclude - refused: no
 * experiment was run" followed by the agent running the experiment is the
 * clearest evidence on the page that something is actually deciding.
 */
export function AgentPanel({
  calls,
  summary,
}: {
  calls: AgentCall[]
  summary: AgentFinishedEvent | null
}) {
  if (calls.length === 0) return null
  return (
    <div className="panel">
      <div className="flex items-baseline justify-between border-b border-rule px-4 py-2.5">
        <span className="legend">What the agent decided to do</span>
        {summary && (
          <span className="legend">
            {summary.tool_calls} tool calls · {summary.llm_calls} model calls
          </span>
        )}
      </div>
      <ol className="divide-y divide-rule/60">
        {calls.map((call, i) => (
          <li key={`${call.tool}-${i}`} className="enter flex gap-3 px-4 py-2.5">
            <span className="meter w-5 shrink-0 text-[0.6875rem] text-legend">
              {i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="text-[0.8125rem] text-bright">
                  {TOOL_PLAIN[call.tool] ?? call.tool}
                </span>
                <span className="meter text-[0.625rem] text-legend">{call.tool}</span>
              </div>
              {call.status === "refused" && call.detail && (
                <p
                  className="mt-1 text-[0.75rem] leading-snug"
                  style={{ color: "var(--color-pending)" }}
                >
                  refused - {call.detail}
                </p>
              )}
            </div>
            <span
              className="legend shrink-0"
              style={{
                color:
                  call.status === "refused"
                    ? "var(--color-pending)"
                    : call.status === "running"
                      ? "var(--color-legend)"
                      : "var(--color-inspec)",
              }}
            >
              {call.status === "running" ? (
                <span className="working">running</span>
              ) : call.status === "refused" ? (
                "refused"
              ) : (
                "done"
              )}
            </span>
          </li>
        ))}
      </ol>
      {summary?.budget_exhausted && (
        <p className="legend border-t border-rule px-4 py-2.5">
          The agent reached its investigation budget before concluding.
        </p>
      )}
    </div>
  )
}
