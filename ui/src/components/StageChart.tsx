"use client"

import type { StageEvent } from "@/lib/types"

/**
 * Loudness across the three pipeline stages, on one scale, against the band.
 *
 * The whole diagnosis is a SHAPE: flat, flat, jump. Three numbers in three boxes
 * make the reader do that comparison in their head; plotted on a shared axis
 * with the tolerance band behind them, the stage that broke it is simply the
 * point outside the green. That is the entire run in one glance.
 *
 * Hand-drawn SVG rather than a charting library: three points and a band do not
 * justify the dependency, and the published page can only load scripts from an
 * allowlist of CDNs.
 */
const H = 110
const PAD_T = 10
const PAD_B = 10

export function StageChart({
  stages,
  target,
  tolerance,
  withheld,
}: {
  stages: StageEvent[]
  target: number
  tolerance: number
  /** Profile cannot be adjudicated here, so no band is drawn to compare against. */
  withheld: boolean
}) {
  if (stages.length === 0) return null

  const values = stages.map((s) => s.integrated_lufs)
  // Always keep the band in frame, even when every measurement sits far from
  // it - a chart that crops the target makes an out-of-spec asset look fine.
  const lo = Math.min(target - tolerance, ...values) - 2
  const hi = Math.max(target + tolerance, ...values) + 2
  const y = (v: number) => PAD_T + ((hi - v) / (hi - lo)) * (H - PAD_T - PAD_B)
  const x = (i: number) => ((i + 0.5) / 3) * 100

  const bandTop = y(target + tolerance)
  const bandHeight = Math.max(2, y(target - tolerance) - bandTop)
  const points = stages.map((s, i) => `${x(i)},${y(s.integrated_lufs)}`).join(" ")

  const colourFor = (s: StageEvent) =>
    s.verdict === "UNMEASURABLE"
      ? "var(--color-pending)"
      : s.verdict === "BLOCKED"
        ? "var(--color-blocked)"
        : "var(--color-inspec)"

  return (
    <div className="border-b border-rule px-4 pt-3 pb-2">
      <div className="flex items-baseline justify-between">
        <span className="legend">Where the audio changed</span>
        <span className="legend">
          {withheld ? "no comparison made" : "green band = acceptable range"}
        </span>
      </div>

      {/* The band and the connecting path stretch to fill the width, so the
          SVG is drawn with preserveAspectRatio="none". That distorts circles
          into ovals, so the measurement points are HTML on top instead - the
          viewBox height matches the pixel height, so the same y() maps to both. */}
      <div className="relative mt-2" style={{ height: H }}>
        <svg
          viewBox={`0 0 100 ${H}`}
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full"
          role="img"
          aria-label={
            withheld
              ? "Loudness measured at each stage; no target comparison is drawn"
              : `Loudness at each stage against a target of ${target} LUFS`
          }
        >
          <rect
            x="0"
            y={bandTop}
            width="100"
            height={bandHeight}
            fill={
              withheld
                ? "color-mix(in srgb, var(--color-legend) 12%, transparent)"
                : "color-mix(in srgb, var(--color-inspec) 18%, transparent)"
            }
          />
          <line
            x1="0"
            x2="100"
            y1={y(target)}
            y2={y(target)}
            stroke={withheld ? "var(--color-rule)" : "var(--color-inspec)"}
            strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke"
          />
          <polyline
            points={points}
            fill="none"
            stroke="var(--color-legend)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        {stages.map((s, i) => (
          <span
            key={s.stage}
            aria-hidden
            className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              left: `${x(i)}%`,
              top: y(s.integrated_lufs),
              background: colourFor(s),
              boxShadow: "0 0 0 2px var(--color-panel)",
            }}
          />
        ))}
      </div>
    </div>
  )
}
