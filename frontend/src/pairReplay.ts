// Display server-side fixed reports; do not recompute matching rules.
import type { PairReplay } from './api/client'

interface DirectionSummary {
  scalars: Record<string, number>
  distributions: Record<
    string,
    { upper_bounds: (number | null)[]; counts: number[] }
  >
}
export interface TrafficReport {
  summaries: Record<
    string,
    {
      window_s: number[]
      summaries: Record<string, Record<string, DirectionSummary>>
    }
  >
  scopes: Record<
    string,
    { distances: Record<string, number>; failed_features: string[] }
  >
  calibration_sha256: string
  policy_sha256: string
}
export const reportOf = (value: PairReplay | null) =>
  value?.traffic_report as TrafficReport | null | undefined

export function scalarRows(
  value: PairReplay | null,
  scope: string,
  direction: string,
) {
  const report = reportOf(value)
  const normal = report?.summaries.normal?.summaries[scope]?.[direction]
  const changed = report?.summaries.changed?.summaries[scope]?.[direction]
  if (!normal || !changed || !report) return []
  return Object.entries(normal.scalars).map(([feature, normalValue]) => ({
    feature,
    normal: normalValue,
    changed: changed.scalars[feature],
    distance: report.scopes[scope]?.distances[`${direction}.${feature}`],
    unmatched: report.scopes[scope]?.failed_features.includes(
      `${direction}.${feature}`,
    ),
  }))
}

export function linePoints(
  member: PairReplay['members'][number],
  cursorS: number,
) {
  const result: [number, number | null][] = []
  for (const point of member.points) {
    if (point.gap_before) result.push([point.sim_elapsed_ms / 1000, null])
    result.push([point.sim_elapsed_ms / 1000, point.value])
  }
  if (member.latest_value === null) result.push([cursorS, null])
  return result
}
