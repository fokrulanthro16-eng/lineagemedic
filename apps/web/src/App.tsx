/**
 * LineageMedic dashboard.
 *
 * Holds the single source of truth for the current incident. Every mutating
 * action re-reads the incident from the backend rather than patching local
 * state, so what is displayed is always what the server actually recorded --
 * particularly for approval state and writeback receipts, where an optimistic
 * client-side update could show an outcome that never happened.
 */
import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from './api/client'
import type { Diagnosis, IntegrationStatus, ScenarioSummary } from './api/types'
import { AgentTimeline } from './components/AgentTimeline'
import { ContainmentPanel } from './components/ContainmentPanel'
import { EvidenceList, McpTrace, QualityChecks } from './components/EvidencePanel'
import { IntegrationBanner } from './components/IntegrationBanner'
import { LineageGraph } from './components/LineageGraph'
import { RemediationPanel } from './components/RemediationPanel'
import { RootCausePanel } from './components/RootCausePanel'
import { ConfidenceMeter, EmptyState, Panel, SeverityBadge } from './components/primitives'

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : String(error)
}

export default function App() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null)
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const [integrations, available] = await Promise.all([api.integrations(), api.scenarios()])
        setStatus(integrations)
        setScenarios(available)
      } catch (err) {
        setError(describe(err))
      }
    })()
  }, [])

  /** Re-fetch the incident so the UI reflects server state, never a guess. */
  const refresh = useCallback(async (incidentId: string) => {
    setDiagnosis(await api.incident(incidentId))
  }, [])

  const run = useCallback(async (scenarioId: string) => {
    setBusy(true)
    setError(null)
    try {
      setDiagnosis(await api.diagnose(scenarioId))
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }, [])

  const decide = useCallback(
    async (approved: boolean) => {
      if (!diagnosis) return
      setBusy(true)
      setError(null)
      try {
        await api.approve(diagnosis.incident_id, approved)
        await refresh(diagnosis.incident_id)
      } catch (err) {
        setError(describe(err))
      } finally {
        setBusy(false)
      }
    },
    [diagnosis, refresh],
  )

  const writeback = useCallback(async () => {
    if (!diagnosis) return
    setBusy(true)
    setError(null)
    try {
      await api.writeback(diagnosis.incident_id)
      await refresh(diagnosis.incident_id)
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }, [diagnosis, refresh])

  return (
    <div className="min-h-screen">
      <IntegrationBanner status={status} />

      <header className="border-b border-slate-800 px-6 py-4">
        <h1 className="text-lg font-semibold text-slate-100">LineageMedic</h1>
        <p className="mt-0.5 text-xs text-slate-500">
          Diagnose, contain, and heal silent data failures before they break production ML.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {scenarios.map((scenario) => (
            <button
              key={scenario.scenario_id}
              type="button"
              onClick={() => void run(scenario.scenario_id)}
              disabled={busy}
              title={scenario.description}
              className="rounded border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-200 hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {scenario.title}
            </button>
          ))}
        </div>
      </header>

      {error ? (
        <p
          data-testid="error-banner"
          className="mx-6 mt-4 rounded border border-critical/50 bg-critical/10 px-3 py-2 text-xs text-red-200"
        >
          {error}
        </p>
      ) : null}

      <main className="space-y-5 p-6">
        {!diagnosis ? (
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-10">
            <EmptyState message="Select a scenario above to run a diagnosis." />
          </div>
        ) : (
          <>
            <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <SeverityBadge severity={diagnosis.severity} />
                    <h2 className="text-base font-semibold text-slate-100">{diagnosis.title}</h2>
                  </div>
                  <p className="mt-2 max-w-3xl text-sm leading-relaxed text-slate-400">
                    {diagnosis.summary}
                  </p>
                  <p className="mt-2 font-mono text-[10px] text-slate-600">
                    {diagnosis.incident_id}
                  </p>
                </div>
                <div className="w-64 shrink-0">
                  <ConfidenceMeter
                    confidence={diagnosis.confidence}
                    explanation={diagnosis.confidence_explanation}
                  />
                </div>
              </div>
              {diagnosis.narration ? (
                <p className="mt-4 border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-400">
                  <span className="font-mono text-[10px] uppercase tracking-wider text-slate-600">
                    narration ({diagnosis.narration_provider}){' '}
                  </span>
                  {diagnosis.narration}
                </p>
              ) : null}
            </section>

            <Panel
              title="Lineage and blast radius"
              subtitle="Selective containment across branches"
            >
              <LineageGraph diagnosis={diagnosis} />
            </Panel>

            <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
              <Panel title="Agent workflow">
                <AgentTimeline diagnosis={diagnosis} />
              </Panel>
              <div className="space-y-5">
                <Panel title="Root cause">
                  <RootCausePanel diagnosis={diagnosis} />
                </Panel>
                <Panel title="Impact">
                  <ContainmentPanel diagnosis={diagnosis} />
                </Panel>
              </div>
            </div>

            <Panel title="Quality measurements" subtitle="Executed against the local warehouse">
              <QualityChecks diagnosis={diagnosis} />
            </Panel>

            <div className="grid gap-5 lg:grid-cols-2">
              <Panel title="Evidence">
                <EvidenceList diagnosis={diagnosis} />
              </Panel>
              <div className="space-y-5">
                <Panel title="Remediation and approval">
                  <RemediationPanel
                    diagnosis={diagnosis}
                    busy={busy}
                    onApprove={() => void decide(true)}
                    onReject={() => void decide(false)}
                    onWriteback={() => void writeback()}
                  />
                </Panel>
                <Panel title="Metadata call trace">
                  <McpTrace diagnosis={diagnosis} />
                </Panel>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
