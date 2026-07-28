/**
 * Execution timeline for the seven agents.
 *
 * All seven are always listed. Agents that did not run in this diagnosis are
 * shown explicitly as "not run" rather than hidden, so the absence of the
 * Writeback agent on an unapproved incident is visible instead of ambiguous.
 */
import { AGENT_LABELS, AGENT_ORDER, type AgentStep, type Diagnosis } from '../api/types'

export function AgentTimeline({ diagnosis }: { diagnosis: Diagnosis }) {
  const steps = diagnosis.steps ?? []
  const byAgent = new Map<string, AgentStep>(steps.map((s) => [s.agent, s]))

  return (
    <ol data-testid="agent-timeline" className="space-y-3">
      {AGENT_ORDER.map((agent, index) => {
        const step = byAgent.get(agent)
        const ran = step !== undefined
        return (
          <li key={agent} className="flex gap-3" data-testid={`agent-step-${agent}`}>
            <div className="flex flex-col items-center">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-semibold ${
                  ran
                    ? step.ok
                      ? 'border-emerald-500/60 bg-emerald-500/15 text-emerald-300'
                      : 'border-critical/60 bg-critical/15 text-red-300'
                    : 'border-slate-700 bg-slate-800/60 text-slate-600'
                }`}
              >
                {index + 1}
              </span>
              {index < AGENT_ORDER.length - 1 ? (
                <span aria-hidden className="mt-1 w-px flex-1 bg-slate-800" />
              ) : null}
            </div>
            <div className="pb-2">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span
                  className={`text-sm font-medium ${ran ? 'text-slate-200' : 'text-slate-600'}`}
                >
                  {AGENT_LABELS[agent]}
                </span>
                {ran ? (
                  <span className="font-mono text-[10px] text-slate-500">{step.duration_ms} ms</span>
                ) : (
                  <span className="text-[10px] uppercase tracking-wider text-slate-600">
                    not run
                  </span>
                )}
              </div>
              <p className={`mt-0.5 text-xs ${ran ? 'text-slate-400' : 'text-slate-600'}`}>
                {ran ? step.summary : 'This agent did not run for this incident.'}
              </p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
