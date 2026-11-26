import type { components } from '../types/api.generated'

export type Health = components['schemas']['Health']
export type ValidationResult = components['schemas']['ValidationResult']
export type RunList = components['schemas']['RunList']
export type RunSnapshot = components['schemas']['RunSnapshot']
export type EventsPage = components['schemas']['EventsPage']
export type ResourceView = components['schemas']['ResourceView']
export type ExperimentRequest = components['schemas']['ExperimentRequest']
export type ExperimentList = components['schemas']['ExperimentList']
export type ExperimentView = components['schemas']['ExperimentView']
export type TemplateCatalog = components['schemas']['TemplateCatalog']
export type ExperimentQuality = components['schemas']['ExperimentQuality']
export type LabelPage = components['schemas']['LabelPage']
export const getExperimentCatalog = () =>
  request<TemplateCatalog>('/api/diagnostics/experiments/catalog')
export const getExperiments = (offset = 0) =>
  request<ExperimentList>(
    `/api/diagnostics/experiments?offset=${offset}&limit=20`,
  )
export const submitExperiment = (body: ExperimentRequest) =>
  request<ExperimentView>('/api/diagnostics/experiments', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
export const getExperimentQuality = (identity: string) =>
  request<ExperimentQuality>(
    `/api/diagnostics/experiments/${encodeURIComponent(identity)}/quality`,
  )
export const getExperimentLabels = (
  identity: string,
  runKey: string,
  offset = 0,
) =>
  request<LabelPage>(
    `/api/diagnostics/experiments/${encodeURIComponent(identity)}/runs/${encodeURIComponent(runKey)}/labels?offset=${offset}&limit=10`,
  )
export const getResources = (key: string) =>
  request<ResourceView>(
    `/api/diagnostics/runs/${encodeURIComponent(key)}/resources`,
  )
export const getEvents = (
  key: string,
  at: string,
  before: string,
  beforeId: string,
) =>
  request<EventsPage>(
    `/api/runs/${encodeURIComponent(key)}/events?at_ns=${at}&before_ns=${before}&before_id=${encodeURIComponent(beforeId)}`,
  )
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
