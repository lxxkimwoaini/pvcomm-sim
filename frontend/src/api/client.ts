import type { components } from '../types/api.generated'

export type Health = components['schemas']['Health']
export type ValidationResult = components['schemas']['ValidationResult']
export type RunList = components['schemas']['RunList']
export type RunSnapshot = components['schemas']['RunSnapshot']
export const getRuns = () => request<RunList>('/api/runs')
export const getSnapshot = (key: string, atNs?: string) =>
  request<RunSnapshot>(
    `/api/runs/${encodeURIComponent(key)}/snapshot?mode=M2${atNs ? `&at_ns=${encodeURIComponent(atNs)}` : ''}`,
  )

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...options,
      signal: AbortSignal.timeout(10000),
    })
  } catch {
    throw new Error('本地服务不可用，请检查后端是否已启动。')
  }
  const data = await response.json()
  if (!response.ok) {
    throw new Error(
      typeof data.detail === 'string'
        ? data.detail
        : '请求校验失败，请检查输入格式。',
    )
  }
  return data as T
}

export const getHealth = () => request<Health>('/api/health')
export const validateConfig = (yamlText: string) =>
  request<ValidationResult>('/api/scenarios/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml_text: yamlText }),
  })
