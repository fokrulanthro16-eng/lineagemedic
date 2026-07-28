/**
 * Anti-fabrication tests for the UI layer.
 *
 * The backend has an equivalent suite. These cover the other half of the
 * promise: that the dashboard *displays* what the backend reported and never
 * dresses up a non-event as a success. Weakening a test here is a change to the
 * product's honesty guarantees, not test maintenance.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { IntegrationBanner } from '../IntegrationBanner'
import { RemediationPanel } from '../RemediationPanel'
import { criticalDiagnosis, fixtureStatus } from '../../test/factories'

const noop = () => {}

describe('fixture-mode labelling', () => {
  it('states plainly that DataHub is not connected', () => {
    render(<IntegrationBanner status={fixtureStatus()} />)

    expect(
      screen.getByText(/Demo Fixture Mode - DataHub integration not connected\./),
    ).toBeInTheDocument()
    // Both integrations must read "not connected", not merely lack a green tick.
    expect(screen.getAllByText('not connected').length).toBeGreaterThanOrEqual(2)
  })

  it('never claims a live connection while in fixture mode', () => {
    render(<IntegrationBanner status={fixtureStatus()} />)

    expect(screen.queryByText(/Live mode/)).not.toBeInTheDocument()
  })

  it('reports a live connection only when the backend says so', () => {
    render(
      <IntegrationBanner
        status={fixtureStatus({
          mode: 'live',
          datahub_connected: true,
          datahub_detail: 'Connected to DataHub GMS.',
          fixture_mode_notice: null,
        })}
      />,
    )

    expect(screen.getByText(/Live mode/)).toBeInTheDocument()
  })
})

describe('writeback receipts', () => {
  it('presents a fixture-mode skip as a non-event, not a success', () => {
    const diagnosis = criticalDiagnosis({
      approval_state: 'approved',
      writeback: {
        status: 'skipped_fixture_mode',
        target_urns: [],
        aspects_written: [],
        tags_added: [],
        note: 'No DataHub instance is connected, so no metadata was written.',
        datahub_urls: [],
        source: 'fixture',
        error: null,
      },
    })

    render(
      <RemediationPanel
        diagnosis={diagnosis}
        busy={false}
        onApprove={noop}
        onReject={noop}
        onWriteback={noop}
      />,
    )

    const receipt = screen.getByTestId('writeback-receipt')
    expect(receipt).toHaveAttribute('data-status', 'skipped_fixture_mode')
    expect(receipt).toHaveTextContent('No writeback performed - fixture mode')
    expect(receipt).not.toHaveTextContent('Metadata written to DataHub')
  })

  it('shows a blocked writeback as blocked', () => {
    const diagnosis = criticalDiagnosis({
      writeback: {
        status: 'blocked_pending_approval',
        target_urns: [],
        aspects_written: [],
        tags_added: [],
        note: 'Approval is required before any metadata is written.',
        datahub_urls: [],
        source: 'fixture',
        error: null,
      },
    })

    render(
      <RemediationPanel
        diagnosis={diagnosis}
        busy={false}
        onApprove={noop}
        onReject={noop}
        onWriteback={noop}
      />,
    )

    expect(screen.getByTestId('writeback-receipt')).toHaveAttribute(
      'data-status',
      'blocked_pending_approval',
    )
  })
})

describe('approval gate', () => {
  it('disables writeback until the incident is approved', () => {
    render(
      <RemediationPanel
        diagnosis={criticalDiagnosis({ approval_state: 'pending' })}
        busy={false}
        onApprove={noop}
        onReject={noop}
        onWriteback={noop}
      />,
    )

    expect(screen.getByRole('button', { name: /Attempt DataHub writeback/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Approve plan/ })).toBeEnabled()
  })

  it('enables writeback once approved, and closes the decision buttons', () => {
    render(
      <RemediationPanel
        diagnosis={criticalDiagnosis({ approval_state: 'approved' })}
        busy={false}
        onApprove={noop}
        onReject={noop}
        onWriteback={noop}
      />,
    )

    expect(screen.getByRole('button', { name: /Attempt DataHub writeback/ })).toBeEnabled()
    // The decision has been made; it must not be silently re-submittable.
    expect(screen.getByRole('button', { name: /Approve plan/ })).toBeDisabled()
  })

  it('marks safety-blocked actions and shows the reason', () => {
    const diagnosis = criticalDiagnosis({
      safety: {
        approved_actions: [],
        blocked_actions: ['quarantine-rows'],
        requires_human_approval: true,
        blocking_reasons: { 'quarantine-rows': 'Targets an asset marked unaffected.' },
        notes: [],
      },
    })

    render(
      <RemediationPanel
        diagnosis={diagnosis}
        busy={false}
        onApprove={noop}
        onReject={noop}
        onWriteback={noop}
      />,
    )

    expect(screen.getByTestId('action-quarantine-rows')).toHaveAttribute('data-blocked', 'true')
    expect(screen.getByText('Targets an asset marked unaffected.')).toBeInTheDocument()
  })
})
