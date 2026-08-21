import assert from 'node:assert/strict'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { discoverPresets } from '@deepseek-ai/dsh-agent-presets'

import {
  ensureClientPackage,
  ensurePresets,
  harnessEnvironment,
  invokedAsMain,
  nodeVersionSupported,
  resolveHomes,
} from '../launcher.mjs'

const LAUNCHER = fileURLToPath(new URL('../launcher.mjs', import.meta.url))
const CLIENT_BRAND = fileURLToPath(new URL('../client-brand', import.meta.url))

test('enforces the DeepSeek Harness Node.js floor numerically', () => {
  assert.equal(nodeVersionSupported('22.18.9'), false)
  assert.equal(nodeVersionSupported('22.19.0'), true)
  assert.equal(nodeVersionSupported('22.9.0'), false)
  assert.equal(nodeVersionSupported('24.0.0'), true)
  assert.equal(nodeVersionSupported('23.9.0'), false)
})

test('recognizes the installed Unix symlink as the launcher entrypoint', () => {
  const root = mkdtempSync(join(tmpdir(), 'evotrace-launcher-link-'))
  const link = join(root, 'evotrace')
  symlinkSync(LAUNCHER, link)
  assert.equal(invokedAsMain(link), true)
})

test('keeps DSH state inside the EvoTrace home by default', () => {
  const homes = resolveHomes({ EVOTRACE_HOME: '/tmp/evotrace-test-home' })
  assert.equal(homes.storeHome, '/tmp/evotrace-test-home')
  assert.equal(homes.dshHome, '/tmp/evotrace-test-home/harness')
})

test('installs one managed sequential orchestrator preset', () => {
  const home = mkdtempSync(join(tmpdir(), 'evotrace-harness-'))
  const root = ensurePresets(home)
  const composition = join(root, 'evotrace-orchestrator', 'agent.cordis.yml')
  assert.equal(existsSync(composition), true)
  const text = readFileSync(composition, 'utf8')
  assert.match(text, /\/plugins\/orchestrator\.mjs/)
  assert.match(text, /toolName: episode_miner_agent/)
  assert.match(text, /toolName: candidate_gate_agent/)
  assert.match(text, /toolName: task_hardener_agent/)
  assert.match(text, /toolName: verifier_critic_agent/)
  assert.match(text, /review_route/)
  assert.match(text, /review_asset_build/)
  assert.match(text, /review_asset_harden/)
  assert.match(text, /review_asset_validate/)
  assert.equal((text.match(/enableRunInBackground: false/g) || []).length, 4)
  assert.equal((text.match(/maxDepth: 1/g) || []).length, 4)
  assert.match(text, /Never\n\s+issue these calls in parallel and never skip a stage/)
})

test('DeepSeek Harness discovers only the healthy EvoTrace orchestrator', async () => {
  const home = mkdtempSync(join(tmpdir(), 'evotrace-harness-'))
  const root = ensurePresets(home)
  const presets = await discoverPresets([{ path: root, trust: 'system' }])
  assert.deepEqual(presets.map(preset => preset.id), ['evotrace-orchestrator'])
  assert.equal(presets.some(preset => preset.broken), false)
})

test('disables DSH telemetry unless explicitly overridden', () => {
  const env = harnessEnvironment({
    EVOTRACE_HOME: '/tmp/evotrace-test-home',
    DSH_TELEMETRY_DISABLED: '',
  })
  assert.equal(env.DSH_TELEMETRY_DISABLED, '1')
  assert.equal(env.DSH_HOME, '/tmp/evotrace-test-home/harness')
})

test('starts new sessions with full access unless explicitly overridden', () => {
  const defaults = harnessEnvironment({
    EVOTRACE_HOME: '/tmp/evotrace-test-home',
  })
  const overridden = harnessEnvironment({
    EVOTRACE_HOME: '/tmp/evotrace-test-home',
    DSH_PERMISSION_MODE: 'workspace-write',
  })

  assert.equal(defaults.DSH_PERMISSION_MODE, 'danger-full-access')
  assert.equal(overridden.DSH_PERMISSION_MODE, 'workspace-write')
})

test('links the EvoTrace client brand into the isolated DSH module fallback', () => {
  const home = mkdtempSync(join(tmpdir(), 'evotrace-harness-'))
  const linked = ensureClientPackage(home)
  assert.equal(existsSync(join(linked, 'package.json')), true)
  assert.match(readFileSync(join(linked, 'lib', 'client.js'), 'utf8'), /document\.title = 'EvoTrace'/)
})

test('migrates an existing EvoTrace-managed client link to the installed release', () => {
  const home = mkdtempSync(join(tmpdir(), 'evotrace-harness-'))
  const oldSource = join(mkdtempSync(join(tmpdir(), 'evotrace-old-release-')), 'harness', 'client-brand')
  mkdirSync(oldSource, { recursive: true })
  writeFileSync(
    join(oldSource, 'package.json'),
    JSON.stringify({ name: '@evotrace/dsh-client-brand', version: '0.7.0' }),
  )
  ensureClientPackage(home, oldSource)

  const linked = ensureClientPackage(home)
  assert.equal(realpathSync(linked), realpathSync(CLIENT_BRAND))
})
