/**
 * Dashboard integration tests against a mocked API module.
 *
 * The approval flow is the important one: the App must re-read the incident
 * from the backend after every mutation, so the screen can only ever show a
 * state the server actually returned.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { ApiError } from '../api/client'
import { criticalDiagnosis, fixtureStatus } from '../test/factories'
import type { ScenarioSummary } from '../api/types'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    api: {
      integrations: vi.fn(),
      scenarios: vi.fn(),
      incident: vi.fn(),
      incidents: vi.fn(),
      diagnose: vi.fn(),
      approve: vi.fn(),
      writeback: vi.fn(),
      audit: vi.fn(),
      reset: vi.fn(),
    },
  }
})

const { api } = await import('../api/client')
const mocked = vi.mocked(api)

const SCENARIOS: ScenarioSummary[] = [
  {
    scenario_id: 'critical',
    title: 'Critical: corrupted ages',
    description: 'Out-of-range ages reach a production endpoint.',
    expected_severity: 'critical',
  },
]

beforeEach(() => {
  mocked.integrations.mockResolvedValue(fixtureStatus())
  mocked.scenarios.mockResolvedValue(SCENARIOS)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('App', () => {
  it('shows the fixture-mode notice and the scenario buttons on load', async () => {
    render(<App />)

    expect(
      await screen.findByText(/Demo Fixture Mode - DataHub integration not connected\./),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Critical: corrupted ages/ })).toBeInTheDocument()
  })

  it('renders a diagnosis after running a scenario', async () => {
    mocked.diagnose.mockResolvedValue(criticalDiagnosis())
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: /Critical: corrupted ages/ }))

    expect(await screen.findByTestId('severity-badge')).toHaveTextContent('critical')
    expect(screen.getByTestId('lineage-graph')).toBeInTheDocument()
    expect(screen.getByTestId('quality-check-age-range')).toHaveTextContent('FAIL')
  })

  it('re-reads the incident from the backend after approval', async () => {
    mocked.diagnose.mockResolvedValue(criticalDiagnosis())
    mocked.approve.mockResolvedValue({
      incident_id: 'LM-TEST-0001',
      approval_state: 'approved',
      message: 'ok',
    })
    mocked.incident.mockResolvedValue(criticalDiagnosis({ approval_state: 'approved' }))

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /Critical: corrupted ages/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Approve plan/ }))

    await waitFor(() => {
      expect(screen.getByTestId('approval-state')).toHaveTextContent('approved')
    })
    // The refetch is what makes the displayed state trustworthy.
    expect(mocked.incident).toHaveBeenCalledWith('LM-TEST-0001')
  })

  it('shows the skipped-writeback receipt returned by the backend', async () => {
    mocked.diagnose.mockResolvedValue(criticalDiagnosis({ approval_state: 'approved' }))
    mocked.writeback.mockResolvedValue({
      status: 'skipped_fixture_mode',
      target_urns: [],
      aspects_written: [],
      tags_added: [],
      note: 'No DataHub instance is connected, so no metadata was written.',
      datahub_urls: [],
      source: 'fixture',
      error: null,
    })
    mocked.incident.mockResolvedValue(
      criticalDiagnosis({
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
      }),
    )

    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: /Critical: corrupted ages/ }))
    await userEvent.click(
      await screen.findByRole('button', { name: /Attempt DataHub writeback/ }),
    )

    const receipt = await screen.findByTestId('writeback-receipt')
    expect(receipt).toHaveAttribute('data-status', 'skipped_fixture_mode')
    expect(receipt).not.toHaveTextContent('Metadata written to DataHub')
  })

  it('surfaces a backend failure instead of rendering invented data', async () => {
    mocked.diagnose.mockRejectedValue(new ApiError('Scenario engine unavailable.', 500))
    render(<App />)

    await userEvent.click(await screen.findByRole('button', { name: /Critical: corrupted ages/ }))

    expect(await screen.findByTestId('error-banner')).toHaveTextContent(
      'Scenario engine unavailable.',
    )
    expect(screen.queryByTestId('severity-badge')).not.toBeInTheDocument()
  })

  it('reports an unreachable backend on initial load', async () => {
    mocked.integrations.mockRejectedValue(new ApiError('Cannot reach the LineageMedic API', 0))
    render(<App />)

    expect(await screen.findByTestId('error-banner')).toHaveTextContent('Cannot reach')
  })
})
