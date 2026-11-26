import { readFileSync } from 'node:fs'
import { test, expect } from '@playwright/test'

test('validates real YAML using the local backend', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '配置校验' }).click()
  await expect(page.getByRole('status').first()).toHaveText(
    'V0.4-development · 服务可用',
  )
  await expect(page.getByLabel('平台开发阶段')).toHaveText('V0.4-development')
  await expect(page.getByRole('button', { name: '校验配置' })).toBeDisabled()
  const config = readFileSync(
    new URL('../../../configs/foundation.yaml', import.meta.url),
    'utf8',
  )
  await page.getByLabel('粘贴 configs/foundation.yaml 的内容').fill(config)
  await page.getByRole('button', { name: '校验配置' }).click()
  await expect(page.getByText('配置校验通过')).toBeVisible()
  await expect(page.getByText('站点数 3 · schema 0.0')).toBeVisible()
  await page
    .getByLabel('粘贴 configs/foundation.yaml 的内容')
    .fill('schema_version: invalid')
  await expect(page.getByText('配置校验通过')).not.toBeVisible()
  await page.getByRole('button', { name: '校验配置' }).click()
  await expect(page.getByText(/配置校验失败/)).toBeVisible()
  await page.screenshot({
    path: 'test-results/foundation-desktop.png',
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(
    page.getByRole('heading', { name: '工程配置校验' }),
  ).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390)
  await page.screenshot({
    path: 'test-results/foundation-mobile.png',
    fullPage: true,
  })
})
