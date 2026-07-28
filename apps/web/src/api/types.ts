/**
 * Domain type aliases over the generated OpenAPI schema.
 *
 * `schema.ts` is generated from the backend's own Pydantic models by
 * `npm run generate:types`, so these names cannot drift from the API contract:
 * if a field changes shape in Python, the regenerated schema breaks the build
 * here rather than failing silently at runtime.
 */
import type { components } from './schema'

type S = components['schemas']

export type Diagnosis = S['Diagnosis']
export type Asset = S['Asset']
export type LineageGraph = S['LineageGraph']
export type QualityCheck = S['QualityCheck']
export type EvidenceItem = S['EvidenceItem']
export type McpCallRecord = S['McpCallRecord']
export type ImpactedAsset = S['ImpactedAsset']
export type ImpactAssessment = S['ImpactAssessment']
export type RootCauseHypothesis = S['RootCauseHypothesis']
export type RemediationAction = S['RemediationAction']
export type SafetyVerdict = S['SafetyVerdict']
export type WritebackReceipt = S['WritebackReceipt']
export type AgentStep = S['AgentStep']
export type ScenarioSummary = S['ScenarioSummary']
export type AuditEvent = S['AuditEvent']
export type IntegrationStatus = S['IntegrationStatus']
export type Owner = S['Owner']

export type Severity = S['Severity']
export type DataSource = S['DataSource']
export type ImpactState = S['ImpactState']
export type CheckStatus = S['CheckStatus']
export type AgentName = S['AgentName']
export type ApprovalState = S['ApprovalState']
export type ActionRisk = S['ActionRisk']
export type WritebackStatus = S['WritebackStatus']

/** The seven agents in execution order, for rendering the timeline skeleton. */
export const AGENT_ORDER: AgentName[] = [
  'quality',
  'context',
  'impact',
  'root_cause',
  'remediation',
  'safety',
  'writeback',
]

export const AGENT_LABELS: Record<AgentName, string> = {
  quality: 'Quality',
  context: 'Context',
  impact: 'Impact',
  root_cause: 'Root Cause',
  remediation: 'Remediation',
  safety: 'Safety',
  writeback: 'Writeback',
}

/**
 * True when the diagnosis was assembled from local fixtures rather than a live
 * DataHub. Drives the mode banner; deliberately derived from the payload's own
 * provenance field rather than from client configuration, so the UI cannot
 * claim a connection the backend did not report.
 */
export function isFixtureMode(diagnosis: Diagnosis): boolean {
  return diagnosis.context_source === 'fixture'
}
