// Synthetic display fixtures; no simulation or matching conclusions are produced.
import { expect, test } from 'vitest'
import type { PairReplay } from '../../src/api/client'
import { linePoints, scalarRows } from '../../src/pairReplay'

test('plots missing intervals and a missing current observation without zero fill', () => {
  const member: PairReplay['members'][number] = {
    member: 'changed',
    run_id: 'synthetic_fixture',
    status: 'completed',
    cursor_utc_ns_exclusive: '1790000000000000000',
    latest_value: null,
    last_observation_age_s: 20,
    source: 'master_iec104_telemetry',
    source_sha256: '0'.repeat(64),
    points: [
      { available_at: '1', sim_elapsed_ms: 1000, value: 10, gap_before: false },
      { available_at: '2', sim_elapsed_ms: 5000, value: 20, gap_before: true },
    ],
  }
  expect(linePoints(member, 25)).toEqual([
    [1, 10],
    [5, null],
    [5, 20],
    [25, null],
  ])
  expect(member.points).toHaveLength(2)
})

test('displays the recorded scope distances and preserves unmatched samples', () => {
  const value = {
    traffic_report: {
      summaries: {
        normal: { summaries: { PV01: { up: { scalars: { packets: 100 } } } } },
        changed: { summaries: { PV01: { up: { scalars: { packets: 200 } } } } },
      },
      scopes: {
        PV01: {
          distances: { 'up.packets': 0.75 },
          failed_features: ['up.packets'],
        },
      },
    },
  } as unknown as PairReplay
  expect(scalarRows(value, 'PV01', 'up')).toEqual([
    {
      feature: 'packets',
      normal: 100,
      changed: 200,
      distance: 0.75,
      unmatched: true,
    },
  ])
  expect(scalarRows(value, 'global', 'up')).toEqual([])
  expect(scalarRows(null, 'PV01', 'up')).toEqual([])
})
