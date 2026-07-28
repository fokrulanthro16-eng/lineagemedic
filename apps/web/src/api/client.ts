/**
 * Typed HTTP client for the LineageMedic backend.
 *
 * Every method returns a type derived from the backend's OpenAPI schema. There
 * are no mock branches and no synthesised fallbacks: if the backend is down or
 * an endpoint fails, the call rejects with {@link ApiError} and the UI shows
 * that failure. A dashboard that invented plausible data on error would be a
 * fabricated result, which this project treats as a correctness bug.
 */
import type {
  ApprovalState,
  AuditEvent,
  Diagnosis,
  IntegrationStatus,
  ScenarioSummary,
  WritebackReceipt,
} from './types'

/** Requests go through the Vite dev-server proxy by default (see vite.config.ts). */
const BASE_URL = import.meta.env['VITE_API_BASE_URL'] ?? '/api'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number, options?: ErrorOptions) {
    super(message, options)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    // Network-level failure: the backend is unreachable. Surface it as such
    // rather than letting a TypeError bubble up as an unrelated crash.
    throw new ApiError(
      `Cannot reach the LineageMedic API at ${BASE_URL}. Is the backend running?`,
      0,
      { cause },
    )
  }

  if (!response.ok) {
    throw new ApiError(await describeFailure(response), response.status)
  }
  return (await response.json()) as T
}

/** Prefer the backend's own error detail; fall back to the status line. */
async function describeFailure(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string' && body.detail) {
      return body.detail
    }
  } catch {
    // Body was not JSON; the status text below is the best available detail.
  }
  return `${response.status} ${response.statusText}`.trim()
}

export const api = {
  integrations: () => request<IntegrationStatus>('/status/integrations'),

  scenarios: () => request<ScenarioSummary[]>('/scenarios'),

  incidents: () => request<Diagnosis[]>('/incidents'),

  incident: (incidentId: string) =>
    request<Diagnosis>(`/incidents/${encodeURIComponent(incidentId)}`),

  diagnose: (scenarioId: string) =>
    request<Diagnosis>('/diagnose', {
      method: 'POST',
      body: JSON.stringify({ scenario_id: scenarioId }),
    }),

  approve: (incidentId: string, approved: boolean, approver = 'operator', note = '') =>
    request<{ incident_id: string; approval_state: ApprovalState; message: string }>(
      `/incidents/${encodeURIComponent(incidentId)}/approve`,
      { method: 'POST', body: JSON.stringify({ approved, approver, note }) },
    ),

  writeback: (incidentId: string) =>
    request<WritebackReceipt>(`/incidents/${encodeURIComponent(incidentId)}/writeback`, {
      method: 'POST',
    }),

  audit: (incidentId?: string) =>
    request<AuditEvent[]>(
      incidentId ? `/audit?incident_id=${encodeURIComponent(incidentId)}` : '/audit',
    ),

  reset: () => request<{ message: string }>('/demo/reset', { method: 'POST' }),
}
