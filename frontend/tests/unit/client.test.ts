// Synthetic HTTP responses are unit-test fixtures only; never simulation evidence.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getHealth, validateConfig } from '../../src/api/client'

afterEach(() => vi.unstubAllGlobals())

describe('API client', () => {
  it('sends user YAML unchanged to the validation endpoint', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ valid: true })))
    vi.stubGlobal('fetch', fetchMock)
    await validateConfig('example: input')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/scenarios/validate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ yaml_text: 'example: input' }),
      }),
    )
  })
  it('reports server validation errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'invalid reference' }), {
          status: 422,
        }),
      ),
    )
    await expect(validateConfig('invalid')).rejects.toThrow('invalid reference')
  })
  it('reports unavailable service without inventing healthy status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(getHealth()).rejects.toThrow('本地服务不可用')
  })
})
