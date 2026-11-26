// Synthetic labels test display semantics only; they are not experiment evidence.
import { createApp } from 'vue'
import { expect, test } from 'vitest'
import LabelTimeline from '../../src/components/LabelTimeline.vue'

function mount(labels: Record<string, unknown>[]) {
  const root = document.createElement('div')
  const app = createApp(LabelTimeline, { labels })
  app.mount(root)
  return { root, app }
}

test('keeps a lawful rejection separate from an unrecovered violation', () => {
  const { root, app } = mount([
    {
      station_id: 'PV01',
      findings: [
        {
          label_status: 'valid',
          service_violation: false,
          outcome: 'rejected',
          start_utc_ns: '1795700904798118905',
          end_utc_ns: null,
          recovery_utc_ns: '1795700904822402992',
        },
      ],
    },
  ])
  expect(root.textContent).toContain('rejected（正常业务结果）')
  expect(root.querySelectorAll('.censored')).toHaveLength(0)
  expect(root.querySelectorAll('.interval.result')).toHaveLength(1)
  app.unmount()
})

test('shows a censored violation and an unknown finding without calling either normal', () => {
  const { root, app } = mount([
    {
      station_id: 'PV02',
      findings: [
        {
          label_status: 'valid',
          service_violation: true,
          outcome: 'confirmation_timeout',
          start_utc_ns: '1795700904798118905',
          end_utc_ns: null,
          observable_at_utc_ns: '1795700904798118905',
          recovery_utc_ns: null,
        },
        {
          label_status: 'uncertain',
          service_violation: null,
          outcome: 'unknown',
          start_utc_ns: '1795700904798118906',
          end_utc_ns: null,
        },
      ],
    },
  ])
  expect(root.querySelectorAll('.interval.violation.censored')).toHaveLength(1)
  expect(root.querySelectorAll('.interval.unknown.censored')).toHaveLength(1)
  expect(root.querySelectorAll('.result')).toHaveLength(0)
  app.unmount()
})
