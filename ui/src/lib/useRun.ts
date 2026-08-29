"use client"

import { useCallback, useRef, useState } from "react"
import type {
  ApprovalEvent,
  ConclusionEvent,
  EvidenceEvent,
  RefusalEvent,
  RepairedEvent,
  RunEvent,
  StageEvent,
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
  error: string | null
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
  error: null,
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

  const start = useCallback(async (fixture: string, reasoner: string) => {
    sourceRef.current?.close()
    setState({ ...EMPTY, running: true })

    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fixture, reasoner }),
    })
    if (!res.ok) {
      setState((s) => ({ ...s, running: false, error: `Could not start run (${res.status})` }))
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
  }, [])

  const approve = useCallback(
    async (runId: string, approved: boolean) => {
      await fetch(`/api/runs/${runId}/approval`, {
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

function reduce(s: RunState, e: RunEvent): RunState {
  switch (e.kind) {
    case "stage":
      return { ...s, stages: [...s.stages, e as StageEvent] }
    case "verdict":
      return { ...s, verdict: e as VerdictEvent }
    case "evidence":
      return { ...s, evidence: [...s.evidence, e as EvidenceEvent] }
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
    case "escalated":
      return { ...s, escalation: String((e as { reason?: string }).reason ?? "") }
    case "error":
      return { ...s, error: String((e as { message?: string }).message ?? "Run failed") }
    case "end":
      return { ...s, running: false }
    default:
      return s
  }
}
