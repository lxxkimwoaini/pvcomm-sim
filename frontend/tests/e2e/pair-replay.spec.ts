import { test, expect } from '@playwright/test'

test('replays retained real pair observations and fixed traffic reports', async ({
  page,
  request,
}, testInfo) => {
  test.skip(
    !process.env.PVCOMM_PAIR_REPLAY_ROOT,
    'requires retained real pair batch; no fixture substitute',
  )
  const listing = await request.get(
    'http://127.0.0.1:8000/api/diagnostics/pairs',
  )
  expect(listing.ok()).toBeTruthy()
  const pairs = (await listing.json()).pairs
  const pair = pairs.find(
    (p: { pair_type: string; business_contrast: boolean }) =>
      p.pair_type === 'P06' && p.business_contrast,
  )
  expect(pair).toBeTruthy()
  const endpoint = `http://127.0.0.1:8000/api/diagnostics/pairs/${pair.pair_key}/replay`
  const response = await request.get(endpoint)
  expect(response.ok()).toBeTruthy()
  const final = await response.json()
  expect(final.members).toHaveLength(2)
  expect(final.traffic_report.calibration_sha256).toBeTruthy()
  expect(final.pair.data_quality_passed).toBe(true)
  expect(final.pair.statistical_match).toBe(false)
  for (const member of final.members) {
    expect(member.source).toBe('master_iec104_telemetry')
    expect(member.points.length).toBeGreaterThan(0)
    expect(
      member.points.every(
        (p: { available_at: string }) =>
          BigInt(p.available_at) < BigInt(member.cursor_utc_ns_exclusive),
      ),
    ).toBe(true)
  }
  const early = await request.get(endpoint + '?at_s=10')
  const earlyData = await early.json()
  expect(earlyData.traffic_report).toBeNull()
  expect(
    earlyData.members.every((m: { points: { sim_elapsed_ms: number }[] }) =>
      m.points.every((p) => p.sim_elapsed_ms < 10001),
    ),
  ).toBe(true)
  expect(
    (
      await request.get(
        `http://127.0.0.1:8000/api/runs/${pair.pair_key}/snapshot`,
      )
    ).status(),
  ).toBe(404)
  await page.goto('/')
  await expect(page.getByTestId('pair-replay')).toHaveCount(0)
  await page.getByRole('button', { name: '科研实验（Oracle）' }).click()
  // Element Plus overlays its readonly input with the selected-item element.
  // Keyboard opening follows the combobox contract without forcing a click
  // through that real overlay.
  await page
    .getByRole('combobox', { name: '配对记录', exact: true })
    .press('ArrowDown')
  await page
    .getByRole('option', {
      name: `${pair.pair_type} · ${pair.pair_id} · ${pair.status}`,
      exact: true,
    })
    .click()
  await expect(page.getByTestId('pair-traffic-table')).toContainText(
    final.traffic_report.calibration_sha256,
  )
  await expect(
    page.getByRole('img', { name: '配对遥测曲线' }).locator('canvas'),
  ).toBeVisible()
  await expect(
    page.getByRole('img', { name: '配对流量分布' }).locator('canvas'),
  ).toBeVisible()
  await page.getByRole('combobox', { name: '配对统计范围' }).press('ArrowDown')
  await page.getByRole('option', { name: 'PV01', exact: true }).click()
  const expectedPackets =
    final.traffic_report.summaries.normal.summaries.PV01.up.scalars.packets
  const packetRow = page
    .getByTestId('pair-traffic-table')
    .locator('tr')
    .filter({ has: page.getByRole('cell', { name: 'packets', exact: true }) })
  await expect(packetRow).toContainText(
    Number(expectedPackets).toLocaleString('zh-CN', {
      maximumFractionDigits: 6,
    }),
  )
  const slider = page.getByRole('slider', { name: '配对场景时间游标' })
  await slider.focus()
  await slider.press('Home')
  await expect(
    page.getByText('游标尚未完成固定分析窗口，隐藏事后统计'),
  ).toBeVisible()
  await expect(page.getByTestId('pair-traffic-table')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
  await slider.focus()
  await slider.press('End')
  await expect(page.getByTestId('pair-traffic-table')).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('real-pair-replay.png'),
    fullPage: true,
  })
})
