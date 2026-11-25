import { readFile, rm } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { URL } from 'node:url'
import process from 'node:process'

const root = new URL('../', import.meta.url)
const temp = new URL('src/types/.api.check.ts', root)
const cli = fileURLToPath(
  new URL('node_modules/openapi-typescript/bin/cli.js', root),
)
try {
  execFileSync(
    process.execPath,
    [cli, '../schemas/openapi.json', '-o', fileURLToPath(temp)],
    {
      cwd: fileURLToPath(root),
      stdio: 'inherit',
    },
  )
  const [expected, actual] = await Promise.all([
    readFile(new URL('src/types/api.generated.ts', root), 'utf8'),
    readFile(temp, 'utf8'),
  ])
  if (expected !== actual)
    throw new Error('Generated API types are stale. Run pnpm types:generate.')
} finally {
  await rm(temp, { force: true })
}
