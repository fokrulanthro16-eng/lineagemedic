/**
 * Persistent integration-status bar.
 *
 * This component carries a correctness requirement, not just a styling one: it
 * must state plainly when the data on screen came from local fixtures rather
 * than a live DataHub. It renders `datahub_connected` as reported by the
 * backend and never infers a connection from the presence of data.
 */
import type { IntegrationStatus } from '../api/types'

function Indicator({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-2" title={detail}>
      <span
        aria-hidden
        className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-slate-600'}`}
      />
      <span className="text-xs text-slate-400">
        {label}:{' '}
        <span className={ok ? 'text-emerald-300' : 'text-slate-300'}>
          {ok ? 'connected' : 'not connected'}
        </span>
      </span>
    </div>
  )
}

export function IntegrationBanner({ status }: { status: IntegrationStatus | null }) {
  if (!status) {
    return (
      <div className="border-b border-slate-800 bg-slate-900 px-6 py-2 text-xs text-slate-500">
        Checking integration status...
      </div>
    )
  }

  const fixtureMode = status.mode === 'fixture'

  return (
    <div
      data-testid="integration-banner"
      className={`border-b px-6 py-2 ${
        fixtureMode ? 'border-amber-900/60 bg-amber-950/40' : 'border-slate-800 bg-slate-900'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        {fixtureMode ? (
          <p className="text-xs font-medium text-amber-300">
            {status.fixture_mode_notice ??
              'Demo Fixture Mode - DataHub integration not connected.'}
          </p>
        ) : (
          <p className="text-xs font-medium text-emerald-300">
            Live mode - reading from a connected DataHub instance.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
          <Indicator ok={status.datahub_connected} label="DataHub" detail={status.datahub_detail} />
          <Indicator ok={status.mcp_connected} label="MCP" detail={status.mcp_detail} />
          <Indicator
            ok={status.llm_available}
            label={`LLM (${status.llm_provider})`}
            detail={status.llm_detail}
          />
        </div>
      </div>
    </div>
  )
}
