/**
 * Test data builders.
 *
 * These are hand-built payloads shaped by the generated API types, used only to
 * drive component tests. They are deliberately *not* used by the application:
 * the running dashboard renders whatever the backend returns and has no
 * built-in sample data to fall back on.
 */
import type { Diagnosis, ImpactedAsset, IntegrationStatus } from '../api/types'

export function fixtureStatus(overrides: Partial<IntegrationStatus> = {}): IntegrationStatus {
  return {
    mode: 'fixture',
    datahub_connected: false,
    datahub_detail: 'Demo Fixture Mode - DataHub integration not connected.',
    mcp_connected: false,
    mcp_detail: 'Demo Fixture Mode - DataHub integration not connected.',
    llm_provider: 'deterministic',
    llm_available: true,
    llm_detail: 'Deterministic narrator; no external model required.',
    fixture_mode_notice: 'Demo Fixture Mode - DataHub integration not connected.',
    ...overrides,
  }
}

function impacted(
  name: string,
  state: ImpactedAsset['state'],
  hops: number | null,
): ImpactedAsset {
  return {
    urn: `urn:li:dataset:(urn:li:dataPlatform:sqlite,${name},PROD)`,
    name,
    kind: 'dataset',
    state,
    hops_from_source: hops,
    rationale: state === 'unaffected' ? 'On an unrelated branch.' : 'Downstream of failing data.',
  }
}

/** A critical incident with a cleared billing branch, mirroring the demo shape. */
export function criticalDiagnosis(overrides: Partial<Diagnosis> = {}): Diagnosis {
  const urn = (n: string) => `urn:li:dataset:(urn:li:dataPlatform:sqlite,${n},PROD)`
  return {
    incident_id: 'LM-TEST-0001',
    scenario_id: 'critical',
    title: 'Invalid ages corrupting readmission features',
    severity: 'critical',
    confidence: 0.87,
    confidence_explanation:
      'Derived from 5 executed checks against fixture data with a clear lineage origin.',
    summary: '37 patient records carry out-of-range ages that reach a production endpoint.',
    quality_checks: [
      {
        check_id: 'age-range',
        description: 'Patient age must be within 0-120.',
        dataset_urn: urn('raw_patients'),
        column: 'age',
        status: 'fail',
        observed_value: 37,
        threshold: 0,
        comparison: 'lte',
        rows_scanned: 500,
        failing_rows: 37,
        sample_failing_values: ['-1', '148', '999'],
      },
    ],
    lineage: {
      source: 'fixture',
      assets: [
        {
          urn: urn('raw_patients'),
          name: 'raw_patients',
          kind: 'dataset',
          platform: 'sqlite',
          upstreams: [],
          downstreams: [urn('staging_patients')],
          source: 'fixture',
        },
        {
          urn: urn('staging_patients'),
          name: 'staging_patients',
          kind: 'dataset',
          platform: 'sqlite',
          upstreams: [urn('raw_patients')],
          downstreams: [],
          source: 'fixture',
        },
        {
          urn: urn('billing_summary'),
          name: 'billing_summary',
          kind: 'dataset',
          platform: 'sqlite',
          upstreams: [],
          downstreams: [],
          source: 'fixture',
        },
      ],
    },
    impact: {
      source_urn: urn('raw_patients'),
      assets: [
        impacted('raw_patients', 'source', 0),
        impacted('staging_patients', 'affected', 1),
        impacted('billing_summary', 'unaffected', null),
      ],
      affected_count: 2,
      unaffected_count: 1,
      production_endpoints_affected: [urn('production_readmission_endpoint')],
      ml_models_affected: [],
    },
    root_causes: [
      {
        summary: 'Defect originates in raw_patients',
        suspected_urn: urn('raw_patients'),
        confidence: 0.9,
        reasoning: 'No upstream asset failed a check, so the defect originates here.',
      },
    ],
    remediation: [
      {
        action_id: 'quarantine-rows',
        title: 'Quarantine invalid rows',
        description: 'Move the 37 out-of-range rows into a quarantine table.',
        risk: 'reversible',
        target_urn: urn('raw_patients'),
        reversible: true,
        rollback: 'Restore rows from the quarantine table.',
        requires_approval: true,
      },
    ],
    safety: {
      approved_actions: ['quarantine-rows'],
      blocked_actions: [],
      requires_human_approval: true,
      blocking_reasons: {},
      notes: [],
    },
    approval_state: 'pending',
    writeback: null,
    evidence: [
      {
        label: 'Quality check age-range',
        detail: '[FAIL] 37 of 500 rows out of range. Samples: -1, 148, 999',
        agent: 'quality',
        source: 'fixture',
      },
    ],
    steps: [
      {
        agent: 'quality',
        title: 'Quality',
        summary: 'Ran 5 checks; 3 failed.',
        started_at: '2026-07-28T12:00:00Z',
        duration_ms: 12,
        ok: true,
      },
      {
        agent: 'impact',
        title: 'Impact',
        summary: '2 affected, 1 cleared.',
        started_at: '2026-07-28T12:00:01Z',
        duration_ms: 4,
        ok: true,
      },
    ],
    mcp_calls: [
      {
        tool: 'get_lineage',
        arguments: { urn: urn('raw_patients') },
        ok: true,
        returned_urns: [urn('raw_patients'), urn('staging_patients')],
        duration_ms: 3,
        source: 'fixture',
      },
    ],
    context_source: 'fixture',
    fixture_mode_notice: 'Demo Fixture Mode - DataHub integration not connected.',
    narration: null,
    narration_provider: 'deterministic',
    created_at: '2026-07-28T12:00:00Z',
    ...overrides,
  }
}
