/**
 * Lineage visualisation with blast radius overlaid.
 *
 * Layout is computed from the graph's own upstream edges rather than from
 * hardcoded coordinates, so an asset added on the backend appears in the right
 * column without touching this file. Columns are longest-path depth, which
 * keeps every edge pointing left-to-right.
 */
import type { Asset, Diagnosis, ImpactState, ImpactedAsset } from '../api/types'

const STATE_STYLES: Record<ImpactState, { box: string; dot: string; label: string }> = {
  source: {
    box: 'border-critical bg-critical/20 text-red-100',
    dot: 'bg-critical',
    label: 'Failing source',
  },
  affected: {
    box: 'border-warning bg-warning/15 text-amber-100',
    dot: 'bg-warning',
    label: 'Downstream affected',
  },
  unaffected: {
    box: 'border-slate-700 bg-slate-800/50 text-slate-400',
    dot: 'bg-cleared',
    label: 'Cleared',
  },
}

const KIND_GLYPH: Record<Asset['kind'], string> = {
  dataset: 'DS',
  feature_table: 'FT',
  ml_model: 'ML',
  endpoint: 'EP',
}

/**
 * Longest-path depth per asset. Longest rather than shortest so a node always
 * sits to the right of every one of its upstreams, which is what makes the
 * left-to-right reading of the diagram true.
 */
export function computeDepths(assets: Asset[]): Map<string, number> {
  const byUrn = new Map(assets.map((a) => [a.urn, a]))
  const depths = new Map<string, number>()

  const visit = (urn: string, seen: Set<string>): number => {
    const cached = depths.get(urn)
    if (cached !== undefined) return cached
    // Cycles are not expected in lineage, but a malformed graph must not hang
    // the dashboard.
    if (seen.has(urn)) return 0

    const asset = byUrn.get(urn)
    if (!asset) return 0
    const parents = (asset.upstreams ?? []).filter((u) => byUrn.has(u))
    const next = new Set(seen).add(urn)
    const depth = parents.length
      ? Math.max(...parents.map((p) => visit(p, next) + 1))
      : 0
    depths.set(urn, depth)
    return depth
  }

  for (const asset of assets) visit(asset.urn, new Set())
  return depths
}

export function LineageGraph({ diagnosis }: { diagnosis: Diagnosis }) {
  const assets = diagnosis.lineage.assets
  const impactByUrn = new Map<string, ImpactedAsset>(
    diagnosis.impact.assets.map((a) => [a.urn, a]),
  )
  const depths = computeDepths(assets)

  const columns = new Map<number, Asset[]>()
  for (const asset of assets) {
    const depth = depths.get(asset.urn) ?? 0
    const bucket = columns.get(depth)
    if (bucket) bucket.push(asset)
    else columns.set(depth, [asset])
  }
  const ordered = [...columns.entries()].sort(([a], [b]) => a - b)

  return (
    <div data-testid="lineage-graph">
      <div className="flex items-stretch gap-3 overflow-x-auto pb-2">
        {ordered.map(([depth, group], columnIndex) => (
          <div key={depth} className="flex items-center gap-3">
            <div className="flex min-w-[190px] flex-col gap-3">
              {group.map((asset) => {
                const impact = impactByUrn.get(asset.urn)
                const state: ImpactState = impact?.state ?? 'unaffected'
                const style = STATE_STYLES[state]
                return (
                  <div
                    key={asset.urn}
                    data-testid={`lineage-node-${asset.name}`}
                    data-state={state}
                    className={`rounded-lg border p-3 ${style.box}`}
                    title={impact?.rationale ?? 'Not part of the assessed blast radius.'}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] opacity-70">
                        {KIND_GLYPH[asset.kind]}
                      </span>
                      <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden />
                    </div>
                    <p className="mt-1 break-words text-sm font-medium">{asset.name}</p>
                    <p className="mt-1 text-[11px] opacity-70">{style.label}</p>
                    {impact?.hops_from_source != null ? (
                      <p className="mt-1 font-mono text-[10px] opacity-60">
                        {impact.hops_from_source} hop
                        {impact.hops_from_source === 1 ? '' : 's'} from source
                      </p>
                    ) : null}
                  </div>
                )
              })}
            </div>
            {columnIndex < ordered.length - 1 ? (
              <span aria-hidden className="select-none text-slate-600">
                &rarr;
              </span>
            ) : null}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 border-t border-slate-800 pt-3">
        {(Object.entries(STATE_STYLES) as [ImpactState, (typeof STATE_STYLES)[ImpactState]][]).map(
          ([state, style]) => (
            <span key={state} className="flex items-center gap-2 text-[11px] text-slate-500">
              <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden />
              {style.label}
            </span>
          ),
        )}
      </div>
    </div>
  )
}
