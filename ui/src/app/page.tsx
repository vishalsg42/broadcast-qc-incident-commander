"use client"

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { BarRail } from "@/components/BarRail"
import { EvidenceTable } from "@/components/EvidenceTable"
import { IngestWait } from "@/components/IngestWait"
import { LoudnessMeter } from "@/components/LoudnessMeter"
import {
  AllowlistPanel,
  AttributionPanel,
  ConclusionPanel,
  ProposalPanel,
  SignalPath,
  UnmeasurablePanel,
} from "@/components/Panels"
import type { ProfileList } from "@/lib/types"
import { useRun } from "@/lib/useRun"

const FIXTURES = [
  { id: "fault", label: "Preset fault" },
  { id: "source-bad", label: "Source out of spec" },
  { id: "clean", label: "Nothing wrong" },
]

export default function ControlRoom() {
  const { state, start, approve } = useRun()
  const [fixture, setFixture] = useState("fault")
  const [reasoner, setReasoner] = useState("scripted")
  const [profileId, setProfileId] = useState<string | null>(null)

  const { data: profiles } = useQuery<ProfileList>({
    queryKey: ["profile"],
    queryFn: async () => (await fetch("/api/profile")).json(),
  })

  // Before the list arrives there is nothing to select; afterwards the server
  // names the default rather than the UI guessing one.
  const selectedId = profileId ?? profiles?.default ?? null
  const profile =
    profiles?.profiles.find((p) => p.id === selectedId) ?? profiles?.profiles[0] ?? null

  const completed = useMemo(() => {
    const done = new Set<string>()
    state.stages.forEach((s) => done.add(s.stage))
    state.evidence.forEach((e) => done.add(e.phase))
    return done
  }, [state.stages, state.evidence])

  const delivered = state.stages.at(-1) ?? null
  const blocked = state.verdict?.status === "BLOCKED"
  const withheld = state.unmeasurable !== null

  return (
    <div className="flex h-dvh overflow-hidden bg-ink">
      <BarRail completed={completed} active={state.running ? null : null} />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* ---- header -------------------------------------------------- */}
        <header className="flex shrink-0 items-center justify-between gap-6 border-b border-rule px-6 py-3">
          <div>
            <h1 className="text-[0.9375rem] font-bold tracking-[0.06em] text-bright uppercase">
              QC Incident Commander
            </h1>
            <p className="mt-0.5 text-[0.8125rem] text-read">
              {profile ? profile.plain : "loading delivery profile"}
            </p>
            {profile && (
              <p className="legend mt-0.5">
                {profile.standard} · {profile.target_lufs} ±{profile.tolerance_lu} LU
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            <select
              value={selectedId ?? ""}
              onChange={(e) => setProfileId(e.target.value)}
              disabled={state.running || !profiles}
              className="legend border border-rule bg-panel px-3 py-2 text-read"
              aria-label="Delivery profile"
            >
              {(profiles?.profiles ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <select
              value={fixture}
              onChange={(e) => setFixture(e.target.value)}
              disabled={state.running}
              className="legend border border-rule bg-panel px-3 py-2 text-read"
              aria-label="Scenario"
            >
              {FIXTURES.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
            <select
              value={reasoner}
              onChange={(e) => setReasoner(e.target.value)}
              disabled={state.running}
              className="legend border border-rule bg-panel px-3 py-2 text-read"
              aria-label="Reasoner"
            >
              <option value="scripted">Scripted</option>
              <option value="gemini">Gemini</option>
            </select>
            <button
              onClick={() => selectedId && start(fixture, reasoner, selectedId)}
              disabled={state.running || !selectedId}
              className="legend cursor-pointer bg-bright px-4 py-2 text-ink transition-opacity hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {state.running ? "Running" : "Run delivery"}
            </button>
          </div>
        </header>

        {/* ---- body ---------------------------------------------------- */}
        <main className="grid min-h-0 flex-1 grid-cols-[minmax(0,2.1fr)_minmax(0,1fr)] items-start gap-4 overflow-y-auto p-4">
          <div className="flex min-w-0 flex-col gap-4">
            <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4">
              <figure className="panel flex flex-col">
                <div className="border-b border-rule px-4 py-2.5">
                  <span className="legend">Deliverable</span>
                </div>
                <video
                  key={state.runId ?? "idle"}
                  className="aspect-video w-full bg-black"
                  // #t=20 seeks past the 10s of mandated head black; a black poster
                  // frame is correct here but reads as a broken player.
                  src="/api/media/master_good.mp4#t=20"
                  preload="metadata"
                  controls
                  muted
                  playsInline
                />
                <figcaption
                  className="flex items-center justify-between px-4 py-2.5"
                  style={{
                    background: blocked
                      ? "color-mix(in srgb, var(--color-blocked) 16%, transparent)"
                      : withheld
                        ? "color-mix(in srgb, var(--color-pending) 16%, transparent)"
                        : undefined,
                  }}
                >
                  <span
                    className="text-sm font-bold tracking-[0.1em] uppercase"
                    style={{
                      color: blocked
                        ? "var(--color-blocked)"
                        : withheld
                          ? "var(--color-pending)"
                          : state.verdict
                            ? "var(--color-inspec)"
                            : "var(--color-legend)",
                    }}
                  >
                    {withheld
                      ? "Not judged"
                      : state.verdict
                        ? blocked
                          ? "Cannot be delivered"
                          : "Ready to deliver"
                        : "Awaiting run"}
                  </span>
                  <span className="meter text-[0.6875rem] text-legend">
                    {state.runId ?? "—"}
                  </span>
                </figcaption>
              </figure>

              <LoudnessMeter
                measured={delivered?.integrated_lufs ?? null}
                target={profile?.target_lufs ?? -23}
                tolerance={profile?.tolerance_lu ?? 0.5}
                blocked={blocked}
                withheld={withheld}
                truePeakCeiling={profile?.true_peak_ceiling ?? null}
                maxBodyBlack={profile?.max_contiguous_body_black_s ?? 1}
              />
            </div>

            <SignalPath
              stages={state.stages}
              activeStage={state.activeStage}
              target={profile?.target_lufs ?? -23}
              tolerance={profile?.tolerance_lu ?? 0.5}
              withheld={withheld}
            />
            {state.ingest && (
              <IngestWait
                backend={state.ingest.backend}
                elapsed={state.ingest.elapsed}
                timeout={state.ingest.timeout}
                found={state.ingest.found}
                expected={state.ingest.expected}
              />
            )}
            <UnmeasurablePanel event={state.unmeasurable} />
            <EvidenceTable rows={state.evidence} activePhase={state.activePhase} />
            <ConclusionPanel conclusion={state.conclusion} />
            <ProposalPanel
              approval={state.approval}
              onDecide={(ok) => state.runId && approve(state.runId, ok)}
              repaired={state.repaired}
              writeBack={state.writeBack}
              escalation={state.escalation}
            />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <AttributionPanel trace={state.trace} />
            <AllowlistPanel
              allowlist={profile?.allowlist ?? []}
              refusals={state.refusals}
            />
            {state.error && (
              <div
                className="panel border-l-2 p-4"
                style={{ borderLeftColor: "var(--color-blocked)" }}
              >
                <span className="legend">Run failed</span>
                <p className="meter mt-2 text-[0.8125rem] text-read">{state.error}</p>
              </div>
            )}
          </div>
        </main>

        <footer className="shrink-0 border-t border-rule px-6 py-2">
          <p className="legend">
            The model interprets and proposes · deterministic code gathers, adjudicates and executes
          </p>
        </footer>
      </div>
    </div>
  )
}
