"use client"

/**
 * An EBU R128 loudness meter, not a stat tile.
 *
 * This is the instrument the engineer actually reads, so the measurement is
 * shown the way their gear shows it: a scale in LU with the target band marked,
 * and the measured value sitting inside or outside it. A number in a box would
 * say the same thing while hiding what "out of spec" means.
 */
const SCALE_MIN = -36
const SCALE_MAX = -10

function pct(lufs: number) {
  const clamped = Math.min(SCALE_MAX, Math.max(SCALE_MIN, lufs))
  return ((clamped - SCALE_MIN) / (SCALE_MAX - SCALE_MIN)) * 100
}

export function LoudnessMeter({
  measured,
  target,
  tolerance,
  blocked,
  withheld,
  truePeakCeiling,
  maxBodyBlack,
}: {
  measured: number | null
  target: number
  tolerance: number
  blocked: boolean
  /** The profile cannot be adjudicated here, so no comparison is drawn. */
  withheld: boolean
  truePeakCeiling: number | null
  maxBodyBlack: number
}) {
  const bandLeft = pct(target - tolerance)
  const bandWidth = pct(target + tolerance) - bandLeft
  // Withheld means the measurement and the target are different quantities.
  // Subtracting one from the other would print a confident number that means
  // nothing - the exact failure this whole system exists to avoid.
  const deviation = measured === null || withheld ? null : measured - target
  const reading = withheld
    ? "var(--color-pending)"
    : blocked
      ? "var(--color-blocked)"
      : "var(--color-inspec)"
  const ticks = [-36, -30, -24, -18, -12]

  return (
    <div className="panel p-5">
      <div className="flex items-baseline justify-between">
        <span className="legend">How loud the programme is</span>
        <span className="legend">
          Target {target.toFixed(1)} ±{tolerance} LU
        </span>
      </div>

      <div className="mt-4 flex items-end gap-5">
        <div
          className="meter text-[3.25rem] leading-none font-semibold"
          style={{
            color: measured === null ? "var(--color-legend)" : reading,
          }}
        >
          {measured === null ? <span className="text-legend">—</span> : measured.toFixed(1)}
        </div>
        <div className="pb-2">
          <div className="legend">LUFS</div>
          {deviation !== null && (
            <div
              className="meter text-sm font-medium"
              style={{ color: blocked ? "var(--color-blocked)" : "var(--color-inspec)" }}
            >
              {Math.abs(deviation).toFixed(1)} LU {deviation >= 0 ? "too loud" : "too quiet"}
            </div>
          )}
          {withheld && measured !== null && (
            <div className="legend" style={{ color: "var(--color-pending)" }}>
              not compared
            </div>
          )}
        </div>
      </div>

      {/* The scale */}
      <div className="relative mt-5 h-9">
        <div className="absolute inset-x-0 top-0 h-4 bg-raised" />
        {/* Target band */}
        <div
          className="absolute top-0 h-4"
          style={{
            left: `${bandLeft}%`,
            width: `${bandWidth}%`,
            background: withheld
              ? "color-mix(in srgb, var(--color-legend) 14%, transparent)"
              : "color-mix(in srgb, var(--color-inspec) 26%, transparent)",
            borderLeft: `1px solid ${withheld ? "var(--color-rule)" : "var(--color-inspec)"}`,
            borderRight: `1px solid ${withheld ? "var(--color-rule)" : "var(--color-inspec)"}`,
          }}
        />
        {/* Measured needle */}
        {measured !== null && (
          <div
            className="absolute top-0 h-4 w-0.5 transition-[left] duration-500 ease-out"
            style={{
              left: `${pct(measured)}%`,
              background: reading,
              boxShadow: `0 0 8px ${reading}`,
            }}
          />
        )}
        {/* Tick legends */}
        {ticks.map((t) => (
          <div key={t} className="absolute top-4" style={{ left: `${pct(t)}%` }}>
            <div className="h-1.5 w-px bg-rule" />
            <div className="meter -translate-x-1/2 pt-1 text-[0.625rem] text-legend">{t}</div>
          </div>
        ))}
      </div>

      <div className="mt-6 border-t border-rule pt-4">
        <span className="legend">The rules being checked</span>
        <dl className="mt-2.5 space-y-1.5">
          <SpecRow
            label="Integrated loudness"
            value={`${target.toFixed(1)} ±${tolerance} LUFS`}
          />
          {truePeakCeiling !== null && (
            <SpecRow label="True peak" value={`≤ ${truePeakCeiling.toFixed(1)} dBTP`} />
          )}
        </dl>

        {/* The black-frame policy is enforced on every run but is never the check
            that fails here, and its rationale is a paragraph. Folded away by
            default: on screen during an incident, it is noise between the
            operator and the number that actually moved. */}
        <details className="group mt-3">
          <summary className="legend cursor-pointer list-none text-legend hover:text-read">
            <span className="group-open:hidden">Black-frame policy ▸</span>
            <span className="hidden group-open:inline">Black-frame policy ▾</span>
          </summary>
          <dl className="mt-2.5 space-y-1.5">
            <SpecRow label="Head black" value="required, 0–10 s" />
            <SpecRow label="Black in body" value={`≤ ${maxBodyBlack} s contiguous`} />
          </dl>
          <p className="mt-2 text-[0.75rem] leading-snug text-legend">
            Black is a policy, not a boolean. Deliverables mandate head black, bars,
            slate and break black — a profile that failed on any black frame would
            reject almost every legitimate master.
          </p>
        </details>
      </div>
    </div>
  )
}

function SpecRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-[0.8125rem] text-legend">{label}</dt>
      <dd className="meter text-[0.8125rem] text-read">{value}</dd>
    </div>
  )
}
