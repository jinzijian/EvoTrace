import assert from 'node:assert/strict'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import * as builder from '../plugins/builder.mjs'
import * as curator from '../plugins/curator.mjs'
import * as orchestrator from '../plugins/orchestrator.mjs'
import * as validator from '../plugins/validator.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')

function activate(plugin) {
  const tools = []
  const commands = []
  const ctx = {
    tools: { register: definition => tools.push(definition) },
    commands: {
      register: definition => {
        commands.push(definition)
        return () => {}
      },
    },
    effect: factory => factory(),
  }
  plugin.apply(ctx)
  return { tools, commands }
}

test('role presets receive different least-privilege tool catalogs', () => {
  const curatorTools = activate(curator).tools.map(tool => tool.name)
  const builderTools = activate(builder).tools.map(tool => tool.name)
  const validatorTools = activate(validator).tools.map(tool => tool.name)

  assert(curatorTools.includes('trajectory_import'))
  assert(!curatorTools.includes('asset_build'))
  assert(builderTools.includes('asset_build'))
  assert(!builderTools.includes('trajectory_import'))
  assert(validatorTools.includes('validation_runs'))
  assert(validatorTools.includes('asset_validate'))
  assert(validatorTools.includes('asset_calibrate'))
  assert(validatorTools.includes('asset_evolve'))
  assert(!validatorTools.includes('asset_build'))
  assert.equal([...curatorTools, ...builderTools, ...validatorTools].includes('bash'), false)
})

test('orchestrator exposes only route-bound mutation tools to model turns', () => {
  const activated = activate(orchestrator)
  const tools = activated.tools.map(tool => tool.name)
  for (const name of [
    'trajectory_show',
    'review_route',
    'review_asset_build',
    'review_asset_harden',
    'review_asset_validate',
    'review_asset_calibrate',
    'validation_runs',
  ]) assert(tools.includes(name), `missing orchestrator tool ${name}`)
  for (const name of ['asset_build', 'asset_harden', 'asset_validate', 'asset_calibrate']) {
    assert(!tools.includes(name), `unbound mutation tool leaked into orchestrator: ${name}`)
  }
  assert(!tools.includes('bash'))
  assert(activated.commands.some(command => command.name === 'review'))
  for (const name of ['build', 'harden', 'validate', 'calibrate']) {
    assert(activated.commands.some(command => command.name === name), `missing slash command ${name}`)
  }
})

test('/review preflights the candidate before steering the sequential pipeline', async () => {
  const reviewed = []
  const review = orchestrator.createReviewCommand({
    preflightCandidate: async candidate => reviewed.push(candidate),
  })
  const messages = []
  const result = await review.handler({
    rawInput: ' 7 ',
    agent: { steer: message => messages.push(message) },
  })
  assert.equal(result.kind, 'success')
  assert.deepEqual(reviewed, ['7'])
  assert.equal(messages.length, 1)
  const text = messages[0].content.map(block => block.text ?? '').join(' ')
  assert.match(text, /Episode Miner -> Candidate Gate -> Task Builder\/Hardener -> Verifier Critic/)
  assert.match(text, /candidate 7/)
  assert.match(text, /Review token: [0-9a-f-]+/)
})

test('/review does not create a model turn when candidate preflight fails', async () => {
  const review = orchestrator.createReviewCommand({
    preflightCandidate: async () => { throw new Error('Candidate not found: smoke-no-data') },
  })
  const messages = []
  const result = await review.handler({
    rawInput: 'smoke-no-data',
    agent: { steer: message => messages.push(message) },
  })
  assert.equal(result.kind, 'error')
  assert.match(result.text, /Candidate not found/)
  assert.equal(messages.length, 0)
})

test('review route is immutable and blocks preference-only mutation or candidate switching', async () => {
  const activated = activate(orchestrator)
  const review = orchestrator.createReviewCommand({ preflightCandidate: async () => {} })
  const messages = []
  await review.handler({
    rawInput: '7',
    agent: { steer: message => messages.push(message) },
  })
  const text = messages[0].content.map(block => block.text ?? '').join(' ')
  const token = text.match(/Review token: ([0-9a-f-]+)/)?.[1]
  assert(token)
  const route = activated.tools.find(tool => tool.name === 'review_route')
  const harden = activated.tools.find(tool => tool.name === 'review_asset_harden')
  const build = activated.tools.find(tool => tool.name === 'review_asset_build')
  await route.execute({
    reviewToken: token,
    candidate: '7',
    route: 'preference_only',
    rationale: 'No coherent executable episode.',
  }, { signal: new AbortController().signal })
  await assert.rejects(
    () => harden.execute({ reviewToken: token, candidate: '7' }, { signal: new AbortController().signal }),
    /route preference_only forbids hardening/,
  )
  await assert.rejects(
    () => build.execute({ reviewToken: token, candidate: '32' }, { signal: new AbortController().signal }),
    /lineage violation: expected 7, received 32/,
  )
  await assert.rejects(
    () => route.execute({
      reviewToken: token,
      candidate: '7',
      route: 'preference_only',
      rationale: 'Try to record the same route twice.',
    }, { signal: new AbortController().signal }),
    /route is immutable/,
  )
  await assert.rejects(
    () => route.execute({
      reviewToken: token,
      candidate: '7',
      route: 'direct',
      rationale: 'Try to change the route.',
    }, { signal: new AbortController().signal }),
    /route is immutable/,
  )
})

test('doctor tool reaches the existing deterministic compiler sidecar', async () => {
  const previous = {
    root: process.env.EVOTRACE_CORE_ROOT,
    python: process.env.EVOTRACE_CORE_PYTHON,
    store: process.env.EVOTRACE_STORE_HOME,
  }
  process.env.EVOTRACE_CORE_ROOT = ROOT
  process.env.EVOTRACE_CORE_PYTHON = join(ROOT, '.venv', 'bin', 'python')
  process.env.EVOTRACE_STORE_HOME = join(mkdtempSync(join(tmpdir(), 'evotrace-plugin-')), 'store')
  try {
    const doctor = activate(curator).tools.find(tool => tool.name === 'evotrace_doctor')
    const output = await doctor.execute({}, { signal: new AbortController().signal })
    assert.match(output, /EvoTrace integrations/)
    assert.match(output, /docker/i)
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      const envKey = key === 'root' ? 'EVOTRACE_CORE_ROOT'
        : key === 'python' ? 'EVOTRACE_CORE_PYTHON'
          : 'EVOTRACE_STORE_HOME'
      if (value === undefined) delete process.env[envKey]
      else process.env[envKey] = value
    }
  }
})
