/**
 * Evidence, quality measurements, and the MCP call trace.
 *
 * Every row is attributed to the agent that produced it and tagged with its
 * provenance, so a reviewer can tell a measured fact from a fixture-derived one
 * without leaving the page.
 */
import { AGENT_LABELS, type Diagnosis, type QualityCheck } from '../api/types'
import { EmptyState, ProvenanceTag } from './primitives'

const COMPARISON_LABEL: Record<QualityCheck['comparison'], string> = {
  lte: 'must be <=',
  gte: 'must be >=',
  eq: 'must equal',
}

export function QualityChecks({ diagnosis }: { diagnosis: Diagnosis }) {
  const checks = diagnosis.quality_checks ?? []
  if (checks.length === 0) return <EmptyState message="No quality checks ran." />

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="pb-2 font-medium">Check</th>
            <th className="pb-2 font-medium">Result</th>
            <th className="pb-2 font-medium">Observed</th>
            <th className="pb-2 font-medium">Threshold</th>
            <th className="pb-2 font-medium">Failing rows</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {checks.map((check) => (
            <tr key={check.check_id} data-testid={`quality-check-${check.check_id}`}>
              <td className="py-2 pr-4">
                <span className="font-mono text-slate-300">{check.check_id}</span>
                <p className="mt-0.5 text-slate-500">{check.description}</p>
              </td>
              <td className="py-2 pr-4">
                <span
                  className={
                    check.status === 'fail'
                      ? 'font-semibold text-red-300'
                      : 'font-semibold text-emerald-300'
                  }
                >
                  {check.status.toUpperCase()}
                </span>
              </td>
              <td className="py-2 pr-4 font-mono text-slate-300">{check.observed_value}</td>
              <td className="py-2 pr-4 font-mono text-slate-500">
                {COMPARISON_LABEL[check.comparison]} {check.threshold}
              </td>
              <td className="py-2 font-mono text-slate-300">
                {check.failing_rows} / {check.rows_scanned}
                {check.sample_failing_values?.length ? (
                  <p className="mt-0.5 text-[10px] text-slate-500">
                    e.g. {check.sample_failing_values.slice(0, 4).join(', ')}
                  </p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function EvidenceList({ diagnosis }: { diagnosis: Diagnosis }) {
  const evidence = diagnosis.evidence ?? []
  if (evidence.length === 0) return <EmptyState message="No evidence was collected." />

  return (
    <ul data-testid="evidence-list" className="space-y-3">
      {evidence.map((item, index) => (
        <li key={`${item.label}-${index}`} className="rounded-lg border border-slate-800 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-slate-200">{item.label}</span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
              {AGENT_LABELS[item.agent]}
            </span>
            <ProvenanceTag source={item.source} />
          </div>
          <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-slate-400">
            {item.detail}
          </p>
        </li>
      ))}
    </ul>
  )
}

export function McpTrace({ diagnosis }: { diagnosis: Diagnosis }) {
  const calls = diagnosis.mcp_calls ?? []
  if (calls.length === 0) {
    return <EmptyState message="No metadata calls were recorded for this incident." />
  }

  return (
    <ul data-testid="mcp-trace" className="space-y-2">
      {calls.map((call, index) => (
        <li
          key={`${call.tool}-${index}`}
          className="flex flex-wrap items-center gap-2 rounded border border-slate-800 px-3 py-2 text-xs"
        >
          <span className={call.ok ? 'text-emerald-400' : 'text-red-400'}>
            {call.ok ? 'ok' : 'error'}
          </span>
          <span className="font-mono text-slate-200">{call.tool}</span>
          <span className="font-mono text-[10px] text-slate-500">{call.duration_ms} ms</span>
          <ProvenanceTag source={call.source} />
          {call.returned_urns?.length ? (
            <span className="text-[10px] text-slate-500">
              returned {call.returned_urns.length} URN
              {call.returned_urns.length === 1 ? '' : 's'}
            </span>
          ) : null}
          {call.error ? <span className="text-[10px] text-red-400">{call.error}</span> : null}
        </li>
      ))}
    </ul>
  )
}
