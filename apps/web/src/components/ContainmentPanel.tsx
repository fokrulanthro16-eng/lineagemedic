/**
 * Affected vs cleared assets.
 *
 * The cleared column is the point of the panel: proving an incident was
 * contained requires naming what was examined and ruled out, not just what
 * broke. Each entry carries the backend's rationale for its classification.
 */
import type { Diagnosis, ImpactedAsset } from '../api/types'
import { EmptyState } from './primitives'

function AssetRow({ asset }: { asset: ImpactedAsset }) {
  return (
    <li
      data-testid={`impact-${asset.name}`}
      className="rounded border border-slate-800 px-3 py-2"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-medium text-slate-200">{asset.name}</span>
        <span className="font-mono text-[10px] uppercase text-slate-500">{asset.kind}</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-slate-500">{asset.rationale}</p>
    </li>
  )
}

export function ContainmentPanel({ diagnosis }: { diagnosis: Diagnosis }) {
  const { impact } = diagnosis
  const affected = impact.assets.filter((a) => a.state !== 'unaffected')
  const cleared = impact.assets.filter((a) => a.state === 'unaffected')

  return (
    <div data-testid="containment-panel" className="grid gap-5 md:grid-cols-2">
      <div>
        <h3 className="mb-2 flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-red-300">
          In blast radius
          <span data-testid="affected-count" className="font-mono text-slate-500">
            {impact.affected_count}
          </span>
        </h3>
        {affected.length ? (
          <ul className="space-y-2">
            {affected.map((a) => (
              <AssetRow key={a.urn} asset={a} />
            ))}
          </ul>
        ) : (
          <EmptyState message="No assets are affected." />
        )}
      </div>
      <div>
        <h3 className="mb-2 flex items-baseline gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
          Examined and cleared
          <span data-testid="cleared-count" className="font-mono text-slate-500">
            {impact.unaffected_count}
          </span>
        </h3>
        {cleared.length ? (
          <ul className="space-y-2">
            {cleared.map((a) => (
              <AssetRow key={a.urn} asset={a} />
            ))}
          </ul>
        ) : (
          <EmptyState message="No assets were ruled out." />
        )}
      </div>
      {impact.production_endpoints_affected?.length ? (
        <p className="md:col-span-2 rounded border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-red-200">
          {impact.production_endpoints_affected.length} production endpoint
          {impact.production_endpoints_affected.length === 1 ? '' : 's'} downstream of the failure.
        </p>
      ) : null}
    </div>
  )
}
