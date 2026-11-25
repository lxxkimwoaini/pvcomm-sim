// Explicit acceptance workload: real API/browser, never synthetic observations.
import process from 'node:process'
import { spawn } from 'node:child_process'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { resolve, join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createHash } from 'node:crypto'
import { chromium, expect } from '@playwright/test'

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, i, all) => {
    if (i % 2 === 0) pairs.push([value.slice(2), all[i + 1]])
    return pairs
  }, []),
)
const sourceLinux = args['source-linux']
const rootLinux = args['root-linux']
const name = args['run-name']
for (const path of [sourceLinux, rootLinux])
  if (!/^\/var\/lib\/pvcomm\/[\w/.-]+$/.test(path ?? ''))
    throw Error('invalid owned Linux path')
if (!/^[\w-]+$/.test(name ?? '')) throw Error('invalid run name')
const output = resolve(args.output)
mkdirSync(output) // Exclusive: never replace a previous experiment.
const rootWindows = rootLinux
  .replace(/^\//, '\\\\wsl.localhost\\Ubuntu\\')
  .replaceAll('/', '\\')
const scenePath = join(rootWindows, name, 'scene.json')
const phaseSeconds = Number(args['phase-seconds'] ?? 1200)
const standalone = args.standalone === 'true'
const startupDeadline = Date.now() + 60000
while (!existsSync(scenePath)) {
  if (Date.now() > startupDeadline) throw Error('scene clock unavailable')
  await delay(250)
}
const scene = JSON.parse(readFileSync(scenePath, 'utf8'))
const originMs = standalone ? Date.now() : scene.start_utc_ns / 1e6
const elapsed = () => (Date.now() - originMs) / 1000
const record = (kind, row) =>
  appendFileSync(
    join(output, `${kind}.jsonl`),
    JSON.stringify({
      ts_utc_ms: Date.now(),
      elapsed_s: elapsed(),
      ...row,
    }) + '\n',
  )
record('phases', { phase: 'off', standalone_replay_preflight: standalone })
let api, vite, monitor, browserServer, browser
const linuxMeasurements = `${rootLinux}/web-measurements-${name}`
const windowsMeasurements = linuxMeasurements
  .replace(/^\//, '\\\\wsl.localhost\\Ubuntu\\')
  .replaceAll('/', '\\')
try {
  while (elapsed() < phaseSeconds) await delay(1000)
  api = spawn(
    'wsl.exe',
    [
      '-d',
      'Ubuntu',
      '--',
      'bash',
      '-lc',
      `cd ${sourceLinux} && PYTHONPATH=${sourceLinux}/src PVCOMM_RUN_ROOT=${rootLinux} /mnt/e/project/pv/.venv-wsl/bin/python scripts/measure_web_api.py --output ${linuxMeasurements} --duration ${Math.ceil(2 * phaseSeconds + 90)}`,
    ],
    { windowsHide: true, stdio: 'pipe' },
  )
  api.stderr.on('data', (chunk) =>
    record('helper', { stderr: chunk.toString() }),
  )
  vite = spawn(
    process.execPath,
    [
      'node_modules/vite/bin/vite.js',
      '--host',
      '127.0.0.1',
      '--port',
      '5173',
      '--strictPort',
    ],
    {
      cwd: resolve('frontend'),
      windowsHide: true,
      stdio: 'pipe',
    },
  )
  vite.stderr.on('data', (chunk) =>
    record('helper', { stderr: chunk.toString() }),
  )
  const readyDeadline = Date.now() + 60000
  while (true) {
    try {
      if (
        (await globalThis.fetch('http://127.0.0.1:8000/api/health')).ok &&
        (await globalThis.fetch('http://127.0.0.1:5173')).ok
      )
        break
    } catch {
      /* Startup probe; deadline below preserves failure. */
    }
    if (Date.now() > readyDeadline) throw Error('Web services not ready')
    await delay(200)
  }
  browserServer = await chromium.launchServer({ headless: true })
  browser = await chromium.connect(browserServer.wsEndpoint())
  monitor = spawn(
    'pwsh.exe',
    [
      '-NoProfile',
      '-File',
      resolve('scripts/measure_windows_processes.ps1'),
      '-TaskRootPid',
      String(process.pid),
      '-Output',
      join(output, 'windows-resources.jsonl'),
      '-StopFile',
      join(output, 'stop'),
    ],
    {
      windowsHide: true,
      stdio: 'pipe',
    },
  )
  monitor.stderr.on('data', (chunk) =>
    record('helper', { monitor_error: chunk.toString() }),
  )
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  })
  page.on('pageerror', (error) => record('errors', { message: error.message }))
  page.on('response', async (response) => {
    if (!response.url().includes('/api/')) return
    try {
      await response.finished()
      const timing = response.request().timing()
      record('requests', {
        url: response.url(),
        status: response.status(),
        phase: elapsed() < 2 * phaseSeconds ? 'live' : 'replay',
        latency_ms: timing.responseEnd,
        server_ms: Number(response.headers()['x-process-time-ms'] ?? 0),
        bytes: (await response.body()).length,
      })
    } catch (error) {
      record('errors', { message: String(error) })
    }
  })
  await page.goto('http://127.0.0.1:5173')
  await page.getByRole('button', { name: '运行与回放' }).click()
  const key = createHash('sha256').update(name).digest('hex').slice(0, 16)
  await page.locator('.run-controls .el-select__wrapper').first().click()
  await page.getByRole('option', { name: new RegExp(key) }).click()
  await expect(page.locator('.topology-cards .station-card')).toHaveCount(30)
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Performance.enable')
  record('phases', {
    phase: 'live',
    browser_pid: browserServer.process().pid,
    api_host: 'WSL localhost',
  })
  let replayStarted = false,
    iteration = 0
  while (elapsed() < 3 * phaseSeconds) {
    if (elapsed() >= 2 * phaseSeconds) {
      if (!replayStarted) {
        record('phases', { phase: 'replay' })
        replayStarted = true
      }
      if (iteration % 5 === 0) {
        const slider = page.locator('.el-slider__runway')
        const box = await slider.boundingBox()
        if (!box) throw Error('replay slider unavailable')
        await slider.click({
          position: {
            x: box.width * (0.1 + (0.8 * ((iteration / 5) % 13)) / 12),
            y: box.height / 2,
          },
        })
      }
    }
    if (iteration % 10 === 0)
      record('browser', {
        metrics: (await cdp.send('Performance.getMetrics')).metrics,
      })
    iteration++
    await delay(1000)
  }
  await page.screenshot({
    path: join(output, 'final-desktop.png'),
    fullPage: true,
  })
  record('phases', { phase: 'completed' })
} catch (error) {
  record('errors', { fatal: true, message: String(error) })
  process.exitCode = 1
} finally {
  writeFileSync(join(output, 'stop'), '')
  if (existsSync(windowsMeasurements))
    writeFileSync(join(windowsMeasurements, 'stop'), '')
  if (browser) await browser.close()
  if (browserServer) await browserServer.close()
  if (vite) vite.kill()
  if (api)
    await Promise.race([
      new Promise((resolveExit) => api.once('exit', resolveExit)),
      delay(15000),
    ])
  if (monitor)
    await Promise.race([
      new Promise((resolveExit) => monitor.once('exit', resolveExit)),
      delay(10000),
    ])
}
