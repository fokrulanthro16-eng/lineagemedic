/** Ranked root-cause hypotheses with the reasoning behind each ranking. */
import type { Diagnosis } from '../api/types'
import { EmptyState } from './primitives'

export function RootCausePanel({ diagnosis }: { diagnosis: Diagnosis }) {
  const hypotheses = diagnosis.root_causes ?? []
  if (hypotheses.length === 0) {
    return <EmptyState message="No root cause to attribute - no checks failed." />
  }

  return (
    <ol data-testid="root-causes" className="space-y-3">
      {hypotheses.map((h, index) => (
        <li
          key={h.suspected_urn}
          data-testid={`root-cause-${index}`}
          className={`rounded-lg border p-3 ${
            index === 0 ? 'border-sky-700/60 bg-sky-950/30' : 'border-slate-800'
          }`}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-xs font-medium text-slate-100">{h.summary}</span>
            <span className="font-mono text-[11px] text-slate-400">
              {Math.round(h.confidence * 100)}%
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{h.reasoning}</p>
          {index === 0 ? (
            <span className="mt-2 inline-block rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-sky-300">
              most likely origin
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  )
}
