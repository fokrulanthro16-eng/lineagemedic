/**
 * Remediation plan, human approval gate, and the writeback receipt.
 *
 * Two honesty rules are enforced here:
 *
 * 1. The writeback button is disabled until the incident is actually approved.
 *    The backend refuses unapproved writebacks independently (403), so this is
 *    a usability affordance rather than the security boundary.
 * 2. The receipt reports the status the backend returned, verbatim. In fixture
 *    mode that is `skipped_fixture_mode`, and this component renders it as a
 *    non-event. It must never present a skipped writeback as an applied one.
 */
import type { ActionRisk, Diagnosis, WritebackReceipt, WritebackStatus } from '../api/types'
import { EmptyState } from './primitives'

const RISK_STYLES: Record<ActionRisk, string> = {
  safe: 'bg-emerald-500/15 text-emerald-300',
  reversible: 'bg-amber-500/15 text-amber-300',
  destructive: 'bg-critical/20 text-red-300',
}

const WRITEBACK_PRESENTATION: Record<
  WritebackStatus,
  { tone: string; headline: string }
> = {
  applied: {
    tone: 'border-emerald-600/50 bg-emerald-950/40 text-emerald-200',
    headline: 'Metadata written to DataHub',
  },
  skipped_fixture_mode: {
    tone: 'border-slate-700 bg-slate-800/50 text-slate-300',
    headline: 'No writeback performed - fixture mode',
  },
  blocked_pending_approval: {
    tone: 'border-amber-700/50 bg-amber-950/30 text-amber-200',
    headline: 'Writeback blocked - approval required',
  },
  failed: {
    tone: 'border-critical/50 bg-critical/10 text-red-200',
    headline: 'Writeback failed',
  },
}

function Receipt({ receipt }: { receipt: WritebackReceipt }) {
  const presentation = WRITEBACK_PRESENTATION[receipt.status]
  return (
    <div
      data-testid="writeback-receipt"
      data-status={receipt.status}
      className={`rounded-lg border p-3 ${presentation.tone}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold">{presentation.headline}</span>
        <span className="font-mono text-[10px] uppercase opacity-70">{receipt.status}</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed opacity-90">{receipt.note}</p>
      {receipt.aspects_written?.length ? (
        <p className="mt-1 font-mono text-[10px] opacity-70">
          aspects: {receipt.aspects_written.join(', ')}
        </p>
      ) : null}
      {receipt.error ? <p className="mt-1 text-[11px] text-red-300">{receipt.error}</p> : null}
    </div>
  )
}

export function RemediationPanel({
  diagnosis,
  busy,
  onApprove,
  onReject,
  onWriteback,
}: {
  diagnosis: Diagnosis
  busy: boolean
  onApprove: () => void
  onReject: () => void
  onWriteback: () => void
}) {
  const actions = diagnosis.remediation ?? []
  const { safety, approval_state: approvalState } = diagnosis
  const blocked = new Set(safety.blocked_actions)
  const decisionPending = approvalState === 'pending'
  const approved = approvalState === 'approved'

  if (actions.length === 0) {
    return <EmptyState message="No remediation is required for this incident." />
  }

  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {actions.map((action) => {
          const isBlocked = blocked.has(action.action_id)
          return (
            <li
              key={action.action_id}
              data-testid={`action-${action.action_id}`}
              data-blocked={isBlocked}
              className={`rounded-lg border p-3 ${
                isBlocked ? 'border-critical/40 bg-critical/5' : 'border-slate-800'
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-slate-200">{action.title}</span>
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${RISK_STYLES[action.risk]}`}
                >
                  {action.risk}
                </span>
                {isBlocked ? (
                  <span className="rounded bg-critical/20 px-1.5 py-0.5 text-[10px] uppercase text-red-300">
                    blocked by safety agent
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
                {action.description}
              </p>
              {action.rollback ? (
                <p className="mt-1 text-[11px] text-slate-500">Rollback: {action.rollback}</p>
              ) : null}
              {isBlocked && safety.blocking_reasons?.[action.action_id] ? (
                <p className="mt-1 text-[11px] text-red-300">
                  {safety.blocking_reasons[action.action_id]}
                </p>
              ) : null}
            </li>
          )
        })}
      </ul>

      {safety.requires_human_approval ? (
        <div
          data-testid="approval-gate"
          className="rounded-lg border border-slate-700 bg-slate-900 p-4"
        >
          <p className="text-xs font-semibold text-slate-200">Human approval required</p>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            LineageMedic never applies remediation or writes metadata without an explicit
            decision. Current state:{' '}
            <span data-testid="approval-state" className="font-mono text-slate-200">
              {approvalState}
            </span>
            .
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onApprove}
              disabled={busy || !decisionPending}
              className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              Approve plan
            </button>
            <button
              type="button"
              onClick={onReject}
              disabled={busy || !decisionPending}
              className="rounded border border-slate-600 px-3 py-1.5 text-xs font-medium text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={onWriteback}
              disabled={busy || !approved}
              title={
                approved
                  ? 'Attempt the DataHub metadata writeback'
                  : 'Approve the plan before attempting a writeback'
              }
              className="rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              Attempt DataHub writeback
            </button>
          </div>
        </div>
      ) : null}

      {diagnosis.writeback ? <Receipt receipt={diagnosis.writeback} /> : null}
    </div>
  )
}
