/** Small shared presentation pieces used across the dashboard panels. */
import type { ReactNode } from 'react'
import type { Severity } from '../api/types'

const SEVERITY_STYLES: Record<Severity, string> = {
  critical: 'bg-critical/15 text-red-300 border-critical/50',
  warning: 'bg-warning/15 text-amber-300 border-warning/50',
  healthy: 'bg-healthy/15 text-emerald-300 border-healthy/50',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      data-testid="severity-badge"
      className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${SEVERITY_STYLES[severity]}`}
    >
      {severity}
    </span>
  )
}

export function Panel({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string
  subtitle?: string
  children: ReactNode
  actions?: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-300">{title}</h2>
          {subtitle ? <p className="mt-1 text-xs text-slate-500">{subtitle}</p> : null}
        </div>
        {actions}
      </header>
      {children}
    </section>
  )
}

/**
 * Confidence rendered as a bar plus the explanation the backend supplied.
 * The explanation is never omitted: a bare percentage with no reasoning is the
 * kind of unfalsifiable metric this project avoids.
 */
export function ConfidenceMeter({
  confidence,
  explanation,
}: {
  confidence: number
  explanation: string
}) {
  const pct = Math.round(confidence * 100)
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wider text-slate-400">Confidence</span>
        <span className="font-mono text-lg text-slate-100">{pct}%</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-sky-500"
          style={{ width: `${pct}%` }}
          role="meter"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Diagnosis confidence"
        />
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-500">{explanation}</p>
    </div>
  )
}

export function ProvenanceTag({ source }: { source: 'live_datahub' | 'fixture' }) {
  const live = source === 'live_datahub'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ${
        live ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700/60 text-slate-400'
      }`}
      title={live ? 'Retrieved from a live DataHub instance' : 'Local demo fixture; not from DataHub'}
    >
      {live ? 'live datahub' : 'fixture'}
    </span>
  )
}

export function EmptyState({ message }: { message: string }) {
  return <p className="py-6 text-center text-sm text-slate-500">{message}</p>
}
