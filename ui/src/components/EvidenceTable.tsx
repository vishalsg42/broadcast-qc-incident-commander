"use client"

import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table"
import type { EvidenceEvent } from "@/lib/types"

const column = createColumnHelper<EvidenceEvent>()

/**
 * The evidence chain.
 *
 * Every row shows the query the CONTROLLER ran alongside the model's reading of
 * it. That pairing is the point: the model supplied `finding` and nothing else,
 * so a reader can check any interpretation against the query that produced it.
 */
export function EvidenceTable({
  rows,
  activePhase,
}: {
  rows: EvidenceEvent[]
  activePhase: string | null
}) {
  const columns = [
    column.accessor("step_id", {
      header: "Step",
      cell: (c) => <span className="meter text-legend">{c.getValue()}</span>,
    }),
    column.accessor("phase", {
      header: "Phase",
      cell: (c) => (
        <span className="legend text-bright">{c.getValue().toLowerCase()}</span>
      ),
    }),
    column.accessor("finding", {
      header: "Model reading",
      cell: (c) => <span className="text-read">{c.getValue()}</span>,
    }),
    column.accessor("query", {
      header: "Query run by the controller",
      cell: (c) => (
        <span className="meter block max-w-[26rem] truncate text-[0.6875rem] text-legend">
          {c.getValue()}
        </span>
      ),
    }),
    column.accessor("supports", {
      header: "",
      cell: (c) => (
        <span
          className="legend"
          style={{ color: c.getValue() ? "var(--color-inspec)" : "var(--color-pending)" }}
          title={c.getValue() ? "Supports the explanation" : "Rules something out"}
        >
          {c.getValue() ? "supports" : "rules out"}
        </span>
      ),
    }),
  ]

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  // Nothing to show and nothing running: render nothing. An empty panel
  // explaining what would eventually appear is just clutter on a first load.
  if (rows.length === 0 && !activePhase) return null

  return (
    <div className="panel overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id} className="border-b border-rule">
              {group.headers.map((header) => (
                <th key={header.id} className="legend px-4 py-2.5 text-left font-semibold">
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="enter border-b border-rule/60 last:border-0">
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-4 py-3 align-top">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {activePhase && (
        <div className="flex items-center gap-3 border-t border-rule px-4 py-3">
          <span className="working legend text-bright">
            {activePhase.toLowerCase()}
          </span>
          <span className="legend">
            controller queried Grafana; the model is reading the result
          </span>
        </div>
      )}
    </div>
  )
}
