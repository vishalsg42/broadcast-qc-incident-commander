"use client"

/**
 * What happens while telemetry becomes queryable.
 *
 * Grafana Cloud's OTLP gateway routinely takes minutes to make a line
 * readable. A silent block for that long is indistinguishable from a hang, so
 * the wait is shown for what it is: a real property of a distributed system,
 * with elapsed time and how many of the expected lines have landed.
 */
export function IngestWait({
  backend,
  elapsed,
  timeout,
  found,
  expected,
}: {
  backend: string
  elapsed: number
  timeout: number
  found: number
  expected: number
}) {
  const pct = Math.min(100, (elapsed / Math.max(timeout, 1)) * 100)
  const late = elapsed > 45

  return (
    <div
      className="panel enter border-l-2 p-4"
      style={{ borderLeftColor: "var(--color-pending)" }}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="legend">Waiting for telemetry - {backend}</span>
        <span className="meter text-sm text-pending">{elapsed.toFixed(0)}s</span>
      </div>

      <div className="mt-3 h-1 w-full bg-raised">
        <div
          className="h-full transition-[width] duration-700 ease-linear"
          style={{ width: `${pct}%`, background: "var(--color-pending)" }}
        />
      </div>

      <div className="mt-3 flex items-baseline justify-between gap-4">
        <p className="text-sm text-read">
          {found} of {expected} QC observations queryable
        </p>
        <span className="meter text-[0.6875rem] text-legend">
          ceiling {timeout.toFixed(0)}s
        </span>
      </div>

      <p className="mt-2 text-[0.75rem] leading-snug text-legend">
        {late
          ? "Ingestion is eventually consistent and per-signal. An un-ingested line is indistinguishable from a missing one, which is why the investigation polls rather than assuming."
          : "The pipeline has finished. The investigation cannot start until its evidence is queryable."}
      </p>
    </div>
  )
}
