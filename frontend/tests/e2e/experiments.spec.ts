import { test, expect } from '@playwright/test'

test('keeps Oracle experiment management separate from the default M2 view', async ({
  page,
  request,
}) => {
  const response = await request.get(
    'http://127.0.0.1:8000/api/diagnostics/experiments/catalog',
  )
  expect(response.ok()).toBeTruthy()
  const catalog = await response.json()
  await page.goto('/')
  await expect(
    page.getByRole('heading', { name: '场景与实验队列' }),
  ).toHaveCount(0)
  await page.getByRole('button', { name: '科研实验（Oracle）' }).click()
  await expect(
    page.getByRole('heading', { name: '场景与实验队列' }),
  ).toBeVisible()
  await expect(
    page.getByText('科研诊断入口：场景、注入及事后标签不属于 M2 在线观测。'),
  ).toBeVisible()
  const submit = page.getByRole('button', { name: '提交到实验队列' })
  if (!catalog.submission_enabled) {
    await expect(submit).toBeDisabled()
    await expect(
      page.getByText('服务端未配置实验队列目录，提交功能未启用。'),
    ).toBeVisible()
  } else await expect(submit).toBeEnabled()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
})

test('reads retained real experiment QC and labels through the Oracle API', async ({
  page,
  request,
}, testInfo) => {
  const identity = process.env.PVCOMM_E2E_EXPERIMENT_ID
  test.skip(
    !identity,
    'requires an actually executed controlled Web request; no fixture substitute',
  )
  if (!identity) return
  const response = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/quality`,
  )
  expect(response.ok()).toBeTruthy()
  const quality = await response.json()
  expect(quality.scope).toBe('Oracle_experiment_management_never_M2_features')
  expect(quality.runs.length).toBeGreaterThan(0)
  await page.goto('/')
  await page.getByRole('button', { name: '科研实验（Oracle）' }).click()
  await page.getByRole('button', { name: identity, exact: true }).click()
  const run = quality.runs[0]
  expect(run.reports.length).toBeGreaterThan(0)
  expect(run.reports.every((r: { passed: boolean }) => r.passed)).toBeTruthy()
  await page
    .getByRole('button', { name: '查询标签时间与原始证据引用' })
    .first()
    .click()
  const labelResponse = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/runs/${run.run_key}/labels`,
  )
  const labels = await labelResponse.json()
  expect(labels.total).toBeGreaterThan(0)
  await expect(page.getByTestId('diagnostic-labels')).toHaveText(
    JSON.stringify(labels.labels, null, 2),
  )
  await page.screenshot({
    path: testInfo.outputPath('real-experiment-quality.png'),
    fullPage: true,
  })
})
