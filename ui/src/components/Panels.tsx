"use client"

import type {
  ApprovalEvent,
  ConclusionEvent,
  RefusalEvent,
  RepairedEvent,
  StageEvent,
  TraceEvent,
  UnmeasurableEvent,
  WriteBackEvent,
} from "@/lib/types"

export function SignalPath({ stages }: { stages: StageEvent[] }) {
  return (
    <div className="panel">
      <div className="border-b border-rule px-4 py-2.5">
        <span className="legend">Signal path</span>
      </div>
      <div className="grid grid-cols-3 divide-x divide-rule">
        {stages.length === 0
          ? ["ingest", "normalize", "package"].map((name) => (
              <div key={name} className="px-4 py-3">
                <div className="legend">{name}</div>
                <div className="meter mt-1 text-lg text-legend">—</div>
              </div>
            ))
          : stages.map((s) => {
              const bad = s.verdict === "BLOCKED"
              // A stage that was never judged must not wear the in-spec marker.
              // Green here would claim a pass the gate explicitly declined to give.
              const withheld = s.verdict === "UNMEASURABLE"
              const mark = withheld
                ? "var(--color-pending)"
                : bad
                  ? "var(--color-blocked)"
                  : "var(--color-inspec)"
              return (
                <div key={s.stage} className="enter px-4 py-3">
                  <div className="flex items-center justify-between">
                    <span className="legend">{s.stage}</span>
                    <span
                      aria-hidden
                      className="h-1.5 w-1.5"
                      style={{
                        background: mark,
                        // Hollow, not filled: measured, but not adjudicated.
                        boxShadow: withheld ? `inset 0 0 0 1px ${mark}` : undefined,
                        opacity: withheld ? 0.5 : 1,
                      }}
                    />
                  </div>
                  <div
                    className="meter mt-1 text-lg"
                    style={{
                      color: bad
                        ? "var(--color-blocked)"
                        : withheld
                          ? "var(--color-read)"
                          : "var(--color-bright)",
                    }}
                  >
                    {s.integrated_lufs.toFixed(1)}
                  </div>
                  <div className="meter mt-1 text-[0.6875rem] text-legend">
                    {s.preset_id} v{s.preset_version}
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
      <span className="legend">Attributed to</span>
      {trace ? (
        <div className="enter mt-2">
          <div className="meter text-lg text-pending">{trace.preset_id}</div>
          <dl className="mt-2 space-y-1 text-[0.8125rem]">
            <Row label="Version" value={`v${trace.preset_version}`} />
            <Row label="Changed" value={trace.preset_changed_at} />
            <Row label="Stage" value={trace.stage} />
            <Row label="Trace" value={trace.trace_id.slice(0, 16)} />
          </dl>
        </div>
      ) : (
        <p className="mt-2 text-sm text-legend">
          Facilities hunt for the preset that changed, not the machine that ran it.
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
        <span className="legend">Escalated — no repair proposed</span>
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
        <span className="legend">Repair proposed — engineer approval required</span>
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
        {event.reason}
      </p>
      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
        <dt className="legend">Profile</dt>
        <dd className="meter text-[0.75rem] text-read">{event.profile_name}</dd>
        <dt className="legend">Requires</dt>
        <dd className="meter text-[0.75rem] text-read">{event.requires}</dd>
      </dl>
      <p className="legend mt-3 leading-relaxed">
        No repair is proposed. A defect cannot be attributed against a
        specification this pipeline is not equipped to measure.
      </p>
    </div>
  )
}
