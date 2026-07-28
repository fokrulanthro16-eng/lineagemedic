/** Rendering tests for the lineage, containment, and timeline panels. */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AgentTimeline } from '../AgentTimeline'
import { ContainmentPanel } from '../ContainmentPanel'
import { LineageGraph, computeDepths } from '../LineageGraph'
import { criticalDiagnosis } from '../../test/factories'
import type { Asset } from '../../api/types'

function asset(name: string, upstreams: string[] = []): Asset {
  return {
    urn: name,
    name,
    kind: 'dataset',
    platform: 'sqlite',
    upstreams,
    downstreams: [],
    source: 'fixture',
  }
}

describe('computeDepths', () => {
  it('places each asset to the right of all of its upstreams', () => {
    const depths = computeDepths([
      asset('a'),
      asset('b', ['a']),
      asset('c', ['a']),
      asset('d', ['b', 'c']),
    ])

    expect(depths.get('a')).toBe(0)
    expect(depths.get('b')).toBe(1)
    expect(depths.get('d')).toBe(2)
  })

  it('uses longest path so a shortcut edge cannot pull a node left of its parent', () => {
    // a -> b -> c, plus a direct a -> c. c must still sit right of b.
    const depths = computeDepths([asset('a'), asset('b', ['a']), asset('c', ['a', 'b'])])

    expect(depths.get('c')).toBe(2)
  })

  it('ignores upstream URNs that are not part of the retrieved subgraph', () => {
    const depths = computeDepths([asset('b', ['not-in-graph'])])

    expect(depths.get('b')).toBe(0)
  })

  it('terminates on a malformed cyclic graph', () => {
    const depths = computeDepths([asset('x', ['y']), asset('y', ['x'])])

    expect(depths.size).toBe(2)
  })
})

describe('LineageGraph', () => {
  it('marks the failing source, the downstream, and the cleared branch distinctly', () => {
    render(<LineageGraph diagnosis={criticalDiagnosis()} />)

    expect(screen.getByTestId('lineage-node-raw_patients')).toHaveAttribute('data-state', 'source')
    expect(screen.getByTestId('lineage-node-staging_patients')).toHaveAttribute(
      'data-state',
      'affected',
    )
    expect(screen.getByTestId('lineage-node-billing_summary')).toHaveAttribute(
      'data-state',
      'unaffected',
    )
  })
})

describe('ContainmentPanel', () => {
  it('names the cleared assets rather than only counting the affected ones', () => {
    render(<ContainmentPanel diagnosis={criticalDiagnosis()} />)

    expect(screen.getByTestId('affected-count')).toHaveTextContent('2')
    expect(screen.getByTestId('cleared-count')).toHaveTextContent('1')
    expect(within(screen.getByTestId('impact-billing_summary')).getByText(/unrelated branch/i))
      .toBeInTheDocument()
  })

  it('flags affected production endpoints', () => {
    render(<ContainmentPanel diagnosis={criticalDiagnosis()} />)

    expect(screen.getByText(/1 production endpoint/)).toBeInTheDocument()
  })
})

describe('AgentTimeline', () => {
  it('lists all seven agents and marks the ones that did not run', () => {
    render(<AgentTimeline diagnosis={criticalDiagnosis()} />)

    expect(screen.getByTestId('agent-step-quality')).toHaveTextContent('Ran 5 checks; 3 failed.')
    // Only quality and impact ran in the factory payload.
    expect(screen.getByTestId('agent-step-writeback')).toHaveTextContent('not run')
    expect(screen.getAllByText('not run')).toHaveLength(5)
  })
})
