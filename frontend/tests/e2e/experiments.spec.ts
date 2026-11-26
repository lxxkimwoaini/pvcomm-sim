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
  await expect(page.getByTestId('label-timeline')).toBeVisible()
  const link = labels.evidence_links.find(
    (item: { reference: { path: string; record_id: string | null } }) =>
      item.reference.path.startsWith('parsed/') &&
      item.reference.record_id !== null,
  )
  expect(link).toBeTruthy()
  const referenceResponse = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/runs/${run.run_key}/labels/${link.label_ordinal}/evidence/${link.evidence_key}`,
  )
  expect(referenceResponse.ok()).toBeTruthy()
  const reference = await referenceResponse.json()
  expect(reference.source_hash_verified).toBe(true)
  expect(reference.capture.frame_numbers.length).toBeGreaterThan(0)
  const pcapResponse = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/runs/${run.run_key}/export/${reference.capture.file_key}`,
  )
  expect(pcapResponse.ok()).toBeTruthy()
  expect(pcapResponse.headers()['x-content-sha256']).toBe(
    reference.capture.sha256,
  )
  expect(['d4c3b2a1', 'a1b2c3d4', '4d3cb2a1', 'a1b23c4d']).toContain(
    (await pcapResponse.body()).subarray(0, 4).toString('hex'),
  )
  await page
    .getByRole('button', {
      name: `${link.reference.path} · ${link.reference.record_id ?? '文件证据'}`,
      exact: true,
    })
    .first()
    .click()
  await expect(page.getByTestId('evidence-record')).toContainText(
    '来源 SHA 核验：通过',
  )
  await page.getByRole('button', { name: '生成只读导出清单' }).first().click()
  const inventoryResponse = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/runs/${run.run_key}/export`,
  )
  expect(inventoryResponse.ok()).toBeTruthy()
  const inventory = await inventoryResponse.json()
  expect(inventory.original_evidence_retained).toBe(true)
  const manifest = inventory.files.find(
    (f: { path: string }) => f.path === 'manifest.json',
  )
  expect(manifest).toBeTruthy()
  await expect(page.getByTestId('export-inventory')).toContainText(
    manifest.sha256,
  )
  const download = await request.get(
    `http://127.0.0.1:8000/api/diagnostics/experiments/${identity}/runs/${run.run_key}/export/${manifest.file_key}`,
  )
  expect(download.ok()).toBeTruthy()
  expect(download.headers()['x-content-sha256']).toBe(manifest.sha256)
  expect((await download.json()).run_id).toBe(run.run_id)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(390)
  await page.screenshot({
    path: testInfo.outputPath('real-experiment-quality.png'),
    fullPage: true,
  })
})
