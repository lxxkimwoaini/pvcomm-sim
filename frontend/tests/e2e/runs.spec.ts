import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from '@playwright/test'

test('shows an honest empty run state', async ({ page }) => {
  test.skip(
    !!process.env.PVCOMM_E2E_REAL_ROOT,
    'real evidence mode uses the separate acceptance test',
  )
  await page.goto('/')
  await page.getByRole('button', { name: '运行与回放' }).click()
  await expect(
    page.getByText('未发现真实运行。', { exact: false }),
  ).toBeVisible()
})

test('reads real evidence and replays only available observations', async ({
  page,
  request,
}) => {
  const root = process.env.PVCOMM_E2E_REAL_ROOT
  test.skip(
    !root,
    'requires retained real c104 evidence; never substituted with mock data',
  )
  if (!root) return
  const runName = process.env.PVCOMM_E2E_RUN_NAME ?? 'v01_case_001'
  const key = createHash('sha256').update(runName).digest('hex').slice(0, 16)
  const eventPath = join(
    root,
    runName,
    'observed',
    'master_events_master.jsonl',
  )
  const observed: {
    event_id: string
    event_type: string
    ioa?: number
    station_id?: string
    available_at: number
    value?: number
  }[] = readFileSync(eventPath, 'utf8')
    .trim()
    .split('\n')
    .map((line) => JSON.parse(line))
  const point = observed.find(
    (row) => row.event_type === 'master_observed_point' && row.ioa === 1001,
  )
  expect(point).toBeDefined()
  const response = await request.get(
    `http://127.0.0.1:8000/api/runs/${key}/snapshot`,
  )
  expect(response.ok()).toBeTruthy()
  const latest = await response.json()
  expect(latest.source).toBe('real_c104_protocol_simulation')
  expect(latest.stations).toHaveLength(
    Number(process.env.PVCOMM_E2E_STATIONS ?? 3),
  )
  expect(JSON.stringify(latest)).not.toMatch(
    /fault_applied|service_violation|p_actual_kw|terminal_command_accepted|code_dirty|interruption/,
  )
  await page.goto('/')
  await page.getByRole('button', { name: '运行与回放' }).click()
  await expect(page.getByRole('region', { name: '运行状态' })).toBeVisible()
  await expect(page.getByRole('button', { name: '跟随最新数据' })).toBeEnabled()
  await page.locator('.run-controls .el-select__wrapper').first().click()
  await page.getByRole('option', { name: new RegExp(key) }).click()
  await expect(
    page.getByRole('heading', { name: '区域通信拓扑' }),
  ).toBeVisible()
  await expect(page.locator('.topology-cards .station-card')).toHaveCount(
    latest.stations.length,
  )
  const latestPower = [...observed]
    .reverse()
    .find(
      (r) => r.event_type === 'master_observed_point' && r.ioa === 1001,
    )?.value
  expect(latestPower).toBeDefined()
  for (const station of latest.stations) {
    for (const measurement of station.measurements ?? []) {
      const raw = observed.filter(
        (r) =>
          r.event_type === 'master_observed_point' &&
          r.station_id === station.station_id &&
          r.ioa === measurement.ioa,
      )
      if (latest.stations.length <= 3) {
        expect(
          measurement.points.map((p: { value: number }) => p.value),
        ).toEqual(raw.slice(-600).map((r) => r.value))
      } else {
        expect(measurement.points.length).toBeLessThanOrEqual(120)
        for (const p of measurement.points)
          expect(raw.some((r) => r.value === p.value)).toBeTruthy()
      }
    }
  }
  await expect(page.locator('.power-reading').first()).toContainText(
    (latest.stations[0].latest_power_kw as number).toFixed(2),
  )
  await expect(page.locator('.power-chart canvas')).toHaveCount(2)
  const slider = page.getByRole('slider')
  await slider.focus()
  await slider.press('End')
  await expect(page.getByText('因果回放', { exact: true })).toBeVisible()
  await slider.press('Home')
  await expect(page.locator('.power-reading').first()).toContainText('未知')
  await page.getByRole('button', { name: '跟随最新数据' }).click()
  await expect(page.locator('.power-reading').first()).toContainText(
    (latest.stations[0].latest_power_kw as number).toFixed(2),
  )
  if (runName.startsWith('v02_') || runName.startsWith('v03_')) {
    await expect(
      page.getByRole('heading', { name: '光伏电气过程' }),
    ).toBeVisible()
    const received = latest.stations[0].measurements as {
      ioa: number
      points: { value: number }[]
    }[]
    for (const ioa of [1003, 1005, 1006, 1008]) {
      const m = received.find((m) => m.ioa === ioa)
      expect(m?.points.length).toBeGreaterThan(0)
    }
    const metric = page.getByRole('combobox', { name: '电气曲线指标' })
    await page
      .locator('.el-select')
      .filter({ has: metric })
      .locator('.el-select__wrapper')
      .click()
    await page.getByRole('option', { name: '辐照度 (W/m²)' }).click()
    await expect(
      page.getByRole('img', { name: '光伏电气遥测曲线' }).locator('canvas'),
    ).toBeVisible()
    await page
      .locator('.el-select')
      .filter({ has: metric })
      .locator('.el-select__wrapper')
      .click()
    await page.getByRole('option', { name: '并网点线电压 (kV)' }).click()
  }
  await page.screenshot({
    path: 'test-results/real-run-desktop.png',
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
  await expect
    .poll(() =>
      page
        .locator('.power-chart')
        .first()
        .evaluate((node) => {
          const canvas = node.querySelector('canvas')
          return canvas
            ? canvas.getBoundingClientRect().width - node.clientWidth
            : 1
        }),
    )
    .toBeLessThanOrEqual(0)
  await page.screenshot({
    path: 'test-results/real-run-mobile.png',
    fullPage: true,
  })
  await page.context().setOffline(true)
  await page.getByRole('button', { name: '刷新运行列表' }).click()
  await expect(
    page.getByRole('alert').filter({ hasText: '本地服务不可用' }),
  ).toBeVisible()
  await page.context().setOffline(false)
  await page.getByRole('button', { name: '刷新运行列表' }).click()
  await expect(
    page.getByRole('alert').filter({ hasText: '本地服务不可用' }),
  ).toHaveCount(0)
})
