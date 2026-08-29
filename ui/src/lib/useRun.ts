"use client"

import { useCallback, useRef, useState } from "react"
import type {
  ApprovalEvent,
  AwaitingTelemetryEvent,
  TelemetryProgressEvent,
  ConclusionEvent,
  EvidenceEvent,
  AgentCall,
  AgentFinishedEvent,
  ExperimentEvent,
  RefusalEvent,
  RepairedEvent,
  RunEvent,
  StageEvent,
  UnmeasurableEvent,
  TraceEvent,
  VerdictEvent,
  WriteBackEvent,
} from "./types"

export interface RunState {
  runId: string | null
  running: boolean
  stages: StageEvent[]
  verdict: VerdictEvent | null
  evidence: EvidenceEvent[]
  trace: TraceEvent | null
  refusals: RefusalEvent[]
  conclusion: ConclusionEvent | null
  approval: ApprovalEvent | null
  repaired: RepairedEvent | null
  writeBack: WriteBackEvent | null
  escalation: string | null
  /** Stage currently being measured by ffmpeg, or null between stages. */
  activeStage: string | null
  /** Investigation phase the model is currently interpreting. */
  activePhase: string | null
  /** Set while the controller is re-running the stage to test the suspect. */
  experimentRunning: boolean
  experiment: ExperimentEvent | null
  /** The tools the agent chose, in the order it chose them. */
  agentCalls: AgentCall[]
  agentSummary: AgentFinishedEvent | null
  unmeasurable: UnmeasurableEvent | null
  error: string | null
  ingest: { backend: string; elapsed: number; timeout: number; found: number; expected: number } | null
}

const EMPTY: RunState = {
  runId: null,
  running: false,
  stages: [],
  verdict: null,
  evidence: [],
  trace: null,
  refusals: [],
  conclusion: null,
  approval: null,
  repaired: null,
  writeBack: null,
  escalation: null,
  activeStage: null,
  activePhase: null,
  experimentRunning: false,
  experiment: null,
  agentCalls: [],
  agentSummary: null,
  unmeasurable: null,
  error: null,
  ingest: null,
}

/**
 * The hosted demo gates anything that starts work behind a token, so the link
 * judges are given carries `?token=...`. Read it back out of the address bar and
 * attach it to every request that starts or approves work - without this the Run
 * button posts unauthenticated and fails with a 403 that looks like a dead page.
 * Locally DEMO_TOKEN is unset, there is no token in the URL, and this is a no-op.
 */
function withToken(path: string): string {
  if (typeof window === "undefined") return path
  const token = new URLSearchParams(window.location.search).get("token")
  if (!token) return path
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`
}

/**
 * Drives one run over SSE.
 *
 * Two-step by necessity: EventSource can only issue GET, so the run is created
 * with POST and then streamed by id. Approval is a separate POST rather than a
 * message on the stream - the investigation stops at an immutable proposal and
 * that proposal is what approval is matched against.
 */
export function useRun() {
  const [state, setState] = useState<RunState>(EMPTY)
  const sourceRef = useRef<EventSource | null>(null)

  const start = useCallback(
    async (fixture: string, reasoner: string, profileId: string) => {
    sourceRef.current?.close()
    setState({ ...EMPTY, running: true })

    const res = await fetch(withToken("/api/runs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fixture, reasoner, profile_id: profileId }),
    })
    if (!res.ok) {
      // 403 means the page was opened without the demo token. Saying so beats a
      // bare status code, because the fix is a different link.
      const detail =
        res.status === 403
          ? "This demo link is missing its access token, so runs cannot be started."
          : `Could not start run (${res.status})`
      setState((s) => ({ ...s, running: false, error: detail }))
      return
    }
    const { run_id } = (await res.json()) as { run_id: string }
    setState((s) => ({ ...s, runId: run_id }))

    const source = new EventSource(`/api/runs/${run_id}/events`)
    sourceRef.current = source

    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent
      setState((s) => reduce(s, event))
      if (event.kind === "end") source.close()
    }
    source.onerror = () => {
      source.close()
      setState((s) => (s.running ? { ...s, running: false, error: "Stream disconnected" } : s))
    }
    },
    [],
  )

  const approve = useCallback(
    async (runId: string, approved: boolean) => {
      await fetch(withToken(`/api/runs/${runId}/approval`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      })
      setState((s) => ({ ...s, approval: null }))
    },
    [],
  )

  return { state, start, approve }
}

function _settle(
  calls: AgentCall[],
  status: AgentCall["status"],
  detail?: string,
): AgentCall[] {
  const i = calls.map((c) => c.status).lastIndexOf("running")
  if (i < 0) return calls
  const next = [...calls]
  next[i] = { ...next[i], status, detail }
  return next
}

function reduce(s: RunState, e: RunEvent): RunState {
  switch (e.kind) {
    case "stage_started":
      return { ...s, activeStage: String((e as { stage?: string }).stage ?? "") }
    case "stage":
      // The stage that just finished is no longer in progress. Cleared here
      // rather than on the next start, so the gap between stages reads as done.
      return { ...s, stages: [...s.stages, e as StageEvent], activeStage: null }
    case "tool_started": {
      const tool = String((e as { tool?: string }).tool ?? "")
      return { ...s, agentCalls: [...s.agentCalls, { tool, status: "running" }] }
    }
    case "tool_result":
      return { ...s, agentCalls: _settle(s.agentCalls, "ok") }
    case "tool_failed":
      // A refused tool is the guardrails working, not the run failing - the
      // agent reads the reason and tries again.
      return {
        ...s,
        agentCalls: _settle(
          s.agentCalls,
          "refused",
          String((e as { error?: string }).error ?? ""),
        ),
      }
    case "agent_finished":
      return { ...s, agentSummary: e as AgentFinishedEvent }
    case "experiment_started":
      return { ...s, experimentRunning: true }
    case "experiment":
      return { ...s, experimentRunning: false, experiment: e as ExperimentEvent }
    case "experiment_failed":
      // Corroboration failing must not read as the investigation failing.
      return { ...s, experimentRunning: false }
    case "phase_started":
      return { ...s, activePhase: String((e as { phase?: string }).phase ?? "") }
    case "verdict":
      return { ...s, verdict: e as VerdictEvent }
    case "evidence":
      return { ...s, evidence: [...s.evidence, e as EvidenceEvent], activePhase: null }
    case "trace":
      return { ...s, trace: e as TraceEvent }
    case "refusal":
      return { ...s, refusals: [...s.refusals, e as RefusalEvent] }
    case "conclusion":
      return { ...s, conclusion: e as ConclusionEvent }
    case "awaiting_approval":
      return { ...s, approval: e as ApprovalEvent }
    case "repaired":
      return { ...s, repaired: e as RepairedEvent }
    case "written_back":
      return { ...s, writeBack: e as WriteBackEvent }
    case "awaiting_telemetry": {
      const e2 = e as AwaitingTelemetryEvent
      return {
        ...s,
        ingest: { backend: e2.backend, elapsed: 0, timeout: e2.timeout_s, found: 0, expected: 3 },
      }
    }
    case "telemetry_progress": {
      const e2 = e as TelemetryProgressEvent
      return {
        ...s,
        ingest: s.ingest
          ? { ...s.ingest, elapsed: e2.elapsed_s, found: e2.found, expected: e2.expected }
          : s.ingest,
      }
    }
    case "telemetry_ready":
      return { ...s, ingest: null }
    case "unmeasurable":
      return { ...s, unmeasurable: e as UnmeasurableEvent }
    case "escalated":
      return { ...s, escalation: String((e as { reason?: string }).reason ?? "") }
    case "error":
      return { ...s, error: String((e as { message?: string }).message ?? "Run failed") }
    case "end":
      return {
        ...s,
        running: false,
        activeStage: null,
        activePhase: null,
        experimentRunning: false,
      }
    default:
      return s
  }
}
