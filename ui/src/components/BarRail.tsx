"use client"

import { BAR_COLORS, STEPS } from "@/lib/types"

/**
 * SMPTE 75% colour bars as the progress spine.
 *
 * The bars are not decoration: the asset takes exactly seven steps through this
 * system - three pipeline stages, then four investigation phases - and 75%
 * colour bars have exactly seven segments. Each lights as its step completes,
 * so the most recognisable artifact in broadcast doubles as the run's status.
 */
export function BarRail({
  completed,
  active,
}: {
  completed: Set<string>
  active: string | null
}) {
  return (
    <aside
      className="flex w-14 shrink-0 flex-col border-r border-rule bg-panel"
      aria-label="Run progress"
    >
      <ol className="flex flex-1 flex-col">
        {STEPS.map((step, i) => {
          const lit = completed.has(step.key)
          const isActive = active === step.key
          return (
            <li
              key={step.key}
              className="bar-seg relative flex flex-1 items-center justify-center"
              style={{ background: BAR_COLORS[i] }}
              data-lit={lit}
              data-active={isActive}
              title={`${step.label}${lit ? " - complete" : isActive ? " - running" : ""}`}
            >
              <span
                className="rotate-180 text-[0.625rem] font-bold tracking-[0.18em] uppercase [writing-mode:vertical-rl]"
                style={{
                  color: "rgb(0 0 0 / 0.78)",
                  textShadow: "0 0 3px rgb(255 255 255 / 0.35)",
                }}
              >
                {step.label}
              </span>
              {step.key === "package" && (
                <span
                  aria-hidden
                  className="absolute right-0 bottom-0 left-0 h-px bg-ink/70"
                />
              )}
            </li>
          )
        })}
      </ol>

      <div className="border-t border-rule px-2 py-3 text-center">
        <div className="legend text-[0.5625rem]">
          {completed.size}/{STEPS.length}
        </div>
      </div>
    </aside>
  )
}
