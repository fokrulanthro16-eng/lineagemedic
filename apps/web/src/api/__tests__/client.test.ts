/** API client behaviour, especially how it reports failure. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../client'
import { criticalDiagnosis } from '../../test/factories'

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({}),
    ...response,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('api client', () => {
  it('posts the scenario id to /diagnose and returns the parsed diagnosis', async () => {
    const diagnosis = criticalDiagnosis()
    const fetchMock = mockFetch({ json: async () => diagnosis })

    const result = await api.diagnose('critical')

    expect(result.incident_id).toBe(diagnosis.incident_id)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/diagnose')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ scenario_id: 'critical' })
  })

  it('surfaces the backend error detail rather than a generic message', async () => {
    mockFetch({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      json: async () => ({ detail: 'Writeback requires prior approval.' }),
    })

    await expect(api.writeback('LM-1')).rejects.toThrow('Writeback requires prior approval.')
  })

  it('reports an unreachable backend instead of throwing a bare TypeError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error = await api.scenarios().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).message).toMatch(/Is the backend running\?/)
  })

  it('falls back to the status line when the error body is not JSON', async () => {
    mockFetch({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => {
        throw new SyntaxError('not json')
      },
    })

    await expect(api.incidents()).rejects.toThrow('500 Internal Server Error')
  })

  it('encodes incident ids into the path', async () => {
    const fetchMock = mockFetch({ json: async () => criticalDiagnosis() })

    await api.incident('LM/1 2')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/incidents/LM%2F1%202')
  })

  it('sends the approval decision', async () => {
    const fetchMock = mockFetch({
      json: async () => ({ incident_id: 'LM-1', approval_state: 'approved', message: 'ok' }),
    })

    await api.approve('LM-1', true)

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toMatchObject({ approved: true, approver: 'operator' })
  })
})
