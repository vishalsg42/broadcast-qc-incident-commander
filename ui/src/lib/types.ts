/** Event shapes emitted by the orchestrator over SSE. */

export type Verdict = "PASS" | "BLOCKED" | "UNKNOWN"

export interface StageEvent {
  kind: "stage"
  stage: string
  preset_id: string
  preset_version: number
  integrated_lufs: number
  verdict: Verdict
}

export interface VerdictEvent {
  kind: "verdict"
  status: Verdict
  target_lufs: number
  tolerance: number
  checks: { check_id: string; status: Verdict; message: string; expected: string }[]
}

export interface EvidenceEvent {
  kind: "evidence"
  phase: "BASELINE" | "DIVERGENCE" | "ACTOR" | "CAUSE"
  step_id: string
  query: string
  query_hash: string
  raw_result_ref: string
  finding: string
  supports: boolean
}

export interface TraceEvent {
  kind: "trace"
  trace_id: string
  stage: string
  preset_id: string
  preset_version: number
  preset_changed_at: string
}

export interface RefusalEvent {
  kind: "refusal"
  name: string
  description: string
  refused: boolean
  reason: string
}

export interface ConclusionEvent {
  kind: "conclusion"
  accepted: boolean
  errors: string[]
  claims: {
    claim_type: string
    claim_value: string
    confidence: string
    cites: string[]
  }[]
}

export interface ApprovalEvent {
  kind: "awaiting_approval"
  action_id: string
  params: Record<string, unknown>
  rationale: string
  allowlist: string[]
}

export interface RepairedEvent {
  kind: "repaired"
  resolved: boolean
  message: string
  output_path: string
  verdict: Verdict | null
}

export interface WriteBackEvent {
  kind: "written_back"
  annotation_ok: boolean
  annotation_detail: string
  incident_ok: boolean
  incident_detail: string
}

export interface GenericEvent {
  kind:
    | "started"
    | "investigation_started"
    | "phase_started"
    | "escalated"
    | "rejected"
    | "repairing"
    | "approval_timeout"
    | "error"
    | "end"
  [key: string]: unknown
}

export type RunEvent =
  | StageEvent
  | VerdictEvent
  | EvidenceEvent
  | TraceEvent
  | RefusalEvent
  | ConclusionEvent
  | ApprovalEvent
  | RepairedEvent
  | WriteBackEvent
  | GenericEvent

export interface Profile {
  id: string
  version: number
  target_lufs: number
  tolerance_lu: number
  true_peak_ceiling: number | null
  max_contiguous_body_black_s: number
  allowlist: string[]
}

/** The asset's seven steps - the same count as SMPTE 75% colour bars. */
export const STEPS = [
  { key: "ingest", label: "Ingest", group: "signal" },
  { key: "normalize", label: "Normalize", group: "signal" },
  { key: "package", label: "Package", group: "signal" },
  { key: "BASELINE", label: "Baseline", group: "investigation" },
  { key: "DIVERGENCE", label: "Divergence", group: "investigation" },
  { key: "ACTOR", label: "Actor", group: "investigation" },
  { key: "CAUSE", label: "Cause", group: "investigation" },
] as const

export const BAR_COLORS = [
  "var(--color-bar-1)",
  "var(--color-bar-2)",
  "var(--color-bar-3)",
  "var(--color-bar-4)",
  "var(--color-bar-5)",
  "var(--color-bar-6)",
  "var(--color-bar-7)",
]
