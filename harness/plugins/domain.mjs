import { defineTool } from '@deepseek-ai/dsh-tools'
import { candidateId, queryText, requireCoreSuccess } from './core-runner.mjs'

const textOutput = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: value }],
}

function tool(name, description, parameters, command) {
  return defineTool({
    name,
    description,
    parameters,
    output: textOutput,
    execute: (args, exec) => requireCoreSuccess(command(args), { signal: exec.signal }),
    presentCall: args => ({ card: 'generic', title: description, kind: 'search', rawInput: args }),
  })
}

const TOOLS = {
  init: () => tool(
    'trajectory_init',
    'Import existing local sessions and refresh deterministic candidate mining in one operation.',
    {
      source: {
        type: 'string',
        enum: ['all', 'codex', 'claude'],
        description: 'Which local session source to initialize. Defaults to all.',
      },
      last: {
        type: 'integer',
        description: 'Optionally inspect only the most recently modified N session files (1-10000).',
      },
    },
    ({ source = 'all', last }) => {
      if (last !== undefined && (last < 1 || last > 10000)) throw new Error('last must be between 1 and 10000')
      return ['init', '--source', source, ...(last === undefined ? [] : ['--last', String(last)])]
    },
  ),
  import: () => tool(
    'trajectory_import',
    'Index existing local Claude Code and/or Codex sessions without modifying them.',
    {
      source: {
        type: 'string',
        enum: ['all', 'codex', 'claude'],
        description: 'Which local session source to import. Defaults to all.',
      },
      last: {
        type: 'integer',
        description: 'Optionally inspect only the most recently modified N session files (1-10000).',
      },
    },
    ({ source = 'all', last }) => {
      if (last !== undefined && (last < 1 || last > 10000)) throw new Error('last must be between 1 and 10000')
      return ['import', source, ...(last === undefined ? [] : ['--last', String(last)])]
    },
  ),
  mine: () => tool(
    'trajectory_mine',
    'Refresh deterministic local scoring and labels for imported trajectories.',
    {},
    () => ['mine'],
  ),
  candidates: () => tool(
    'trajectory_candidates',
    'List high-signal trajectory candidates and their evidence-backed labels.',
    {
      limit: { type: 'integer', description: 'Maximum candidates to return (1-100).' },
    },
    ({ limit = 20 }) => {
      if (limit < 1 || limit > 100) throw new Error('limit must be between 1 and 100')
      return ['candidates', '--limit', String(limit)]
    },
  ),
  search: () => tool(
    'trajectory_search',
    'Search mined trajectory tasks, evidence, and repository names.',
    {
      query: { type: 'string', required: true, description: 'Text to search for.' },
      limit: { type: 'integer', description: 'Maximum matches to return (1-100).' },
    },
    ({ query, limit = 20 }) => {
      if (limit < 1 || limit > 100) throw new Error('limit must be between 1 and 100')
      return ['search', queryText(query), '--limit', String(limit)]
    },
  ),
  show: () => tool(
    'trajectory_show',
    'Inspect one candidate, its provenance, labels, and readiness gaps.',
    {
      candidate: { type: 'string', required: true, description: 'Candidate number, session id, or id prefix.' },
    },
    ({ candidate }) => ['show', candidateId(candidate)],
  ),
  build: () => tool(
    'asset_build',
    'Compile a selected execution-verifiable candidate into a Docker-ready asset bundle. This does not validate its verifier.',
    {
      candidate: { type: 'string', required: true, description: 'Candidate number, session id, or id prefix.' },
      rebuild: { type: 'boolean', description: 'Rebuild the asset and archive the previous generated bundle.' },
    },
    ({ candidate, rebuild = false }) => ['build', candidateId(candidate), ...(rebuild ? ['--rebuild'] : [])],
  ),
  assets: () => tool(
    'asset_list',
    'List compiled EvoTrace assets and their current readiness state.',
    {
      limit: { type: 'integer', description: 'Maximum assets to return (1-100).' },
    },
    ({ limit = 20 }) => {
      if (limit < 1 || limit > 100) throw new Error('limit must be between 1 and 100')
      return ['assets', '--limit', String(limit)]
    },
  ),
  runs: () => tool(
    'validation_runs',
    'List saved validation runs. Absence of a conforming Docker run means the asset is not verified.',
    {
      limit: { type: 'integer', description: 'Maximum runs to return (1-100).' },
    },
    ({ limit = 20 }) => {
      if (limit < 1 || limit > 100) throw new Error('limit must be between 1 and 100')
      return ['runs', '--limit', String(limit)]
    },
  ),
  validate: () => tool(
    'asset_validate',
    'Build the asset image and independently test its base and reference states in a locked-down Docker runtime. A passing two-state run marks the current bundle digest Verified.',
    {
      candidate: { type: 'string', required: true, description: 'Candidate number, session id, or id prefix.' },
      timeout: { type: 'integer', description: 'Per-state validation timeout in seconds (1-3600).' },
      rebuildImage: { type: 'boolean', description: 'Rebuild the Docker image even when the exact bundle digest is cached.' },
    },
    ({ candidate, timeout = 900, rebuildImage = false }) => {
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      return [
        'validate',
        candidateId(candidate),
        '--timeout',
        String(timeout),
        ...(rebuildImage ? ['--rebuild-image'] : []),
      ]
    },
  ),
  calibrate: () => tool(
    'asset_calibrate',
    'Run independent DeepSeek Harness solver attempts against a task-only workspace, score every patch in Docker, and adapt hints or a reference-gated verifier overlay toward a target pass count. This sends task context to the configured model provider.',
    {
      candidate: { type: 'string', required: true, description: 'Candidate number, session id, or id prefix.' },
      trials: { type: 'integer', description: 'Independent attempts per difficulty version (1-20, default 5).' },
      targetPasses: { type: 'integer', description: 'Desired passes per version (default 2).' },
      maxRounds: { type: 'integer', description: 'Maximum hint/verifier adjustment rounds (1-10, default 3).' },
      timeout: { type: 'integer', description: 'Timeout per solver and verifier attempt in seconds (1-3600).' },
      initialHints: { type: 'integer', description: 'Number of automatically derived hints shown in round one.' },
    },
    ({ candidate, trials = 5, targetPasses = 2, maxRounds = 3, timeout = 600, initialHints = 0 }) => {
      if (trials < 1 || trials > 20) throw new Error('trials must be between 1 and 20')
      if (targetPasses < 0 || targetPasses > trials) throw new Error('targetPasses must be between 0 and trials')
      if (maxRounds < 1 || maxRounds > 10) throw new Error('maxRounds must be between 1 and 10')
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      if (initialHints < 0 || initialHints > 10) throw new Error('initialHints must be between 0 and 10')
      return [
        'calibrate',
        candidateId(candidate),
        '--trials',
        String(trials),
        '--target-passes',
        String(targetPasses),
        '--max-rounds',
        String(maxRounds),
        '--timeout',
        String(timeout),
        '--initial-hints',
        String(initialHints),
        '--allow-upload',
      ]
    },
  ),
  harden: () => tool(
    'asset_harden',
    'Derive a semantically harder child task from an exact verified parent, validate it in Docker, then self-play it toward the target pass count. This sends the solved seed task to the configured model provider.',
    {
      candidate: { type: 'string', required: true, description: 'Verified parent candidate number, session id, or id prefix.' },
      trials: { type: 'integer', description: 'Independent attempts for the child task (1-20, default 5).' },
      targetPasses: { type: 'integer', description: 'Desired passes for the child version (default 2).' },
      timeout: { type: 'integer', description: 'Timeout per hardening, verifier, and solver call in seconds (1-3600).' },
    },
    ({ candidate, trials = 5, targetPasses = 2, timeout = 600 }) => {
      if (trials < 1 || trials > 20) throw new Error('trials must be between 1 and 20')
      if (targetPasses < 0 || targetPasses > trials) throw new Error('targetPasses must be between 0 and trials')
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      return [
        'harden',
        candidateId(candidate),
        '--trials',
        String(trials),
        '--target-passes',
        String(targetPasses),
        '--timeout',
        String(timeout),
        '--allow-upload',
      ]
    },
  ),
  evolve: () => tool(
    'asset_evolve',
    'Generate execution-grounded repository experience, compress it, and test whether it improves paired DeepSeek Harness attempts on a held-out same-repository task. This sends task context and a sanitized trajectory capsule to the configured model provider.',
    {
      source: { type: 'string', required: true, description: 'Source candidate number, session id, or id prefix.' },
      heldOut: { type: 'string', description: 'Independent same-repository asset. Omit only for a non-certifying wiring smoke test.' },
      trials: { type: 'integer', description: 'Paired baseline/conditioned trials (1-20, default 5).' },
      targetPasses: { type: 'integer', description: 'Desired baseline passes used by curriculum feedback (default 2).' },
      timeout: { type: 'integer', description: 'Timeout per exploration, compression, solver, and verifier call in seconds (1-3600).' },
    },
    ({ source, heldOut, trials = 5, targetPasses = 2, timeout = 600 }) => {
      if (trials < 1 || trials > 20) throw new Error('trials must be between 1 and 20')
      if (targetPasses < 0 || targetPasses > trials) throw new Error('targetPasses must be between 0 and trials')
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      return [
        'evolve',
        candidateId(source),
        ...(heldOut ? ['--held-out', candidateId(heldOut)] : []),
        '--trials',
        String(trials),
        '--target-passes',
        String(targetPasses),
        '--timeout',
        String(timeout),
        '--allow-upload',
      ]
    },
  ),
  doctor: () => tool(
    'evotrace_doctor',
    'Check the local EvoTrace core, session sources, Git, and Docker integration.',
    {},
    () => ['doctor'],
  ),
}

const COMMANDS = {
  init: {
    description: 'Import local history and refresh candidate mining',
    hint: '[all|codex|claude]',
    parse(raw) {
      const source = raw.trim() || 'all'
      if (!['all', 'codex', 'claude'].includes(source)) throw new Error('Usage: /init [all|codex|claude]')
      return ['init', '--source', source]
    },
  },
  import: {
    description: 'Index local Claude Code and Codex sessions',
    hint: '[all|codex|claude]',
    parse(raw) {
      const source = raw.trim() || 'all'
      if (!['all', 'codex', 'claude'].includes(source)) throw new Error('Usage: /import [all|codex|claude]')
      return ['import', source]
    },
  },
  mine: { description: 'Refresh candidate mining', parse: noInput('/mine', ['mine']) },
  candidates: { description: 'Browse valuable trajectories', parse: noInput('/candidates', ['candidates']) },
  search: {
    description: 'Search mined trajectories',
    hint: '<words>',
    parse(raw) { return ['search', queryText(raw)] },
  },
  show: {
    description: 'Inspect one candidate',
    hint: '<number-or-id>',
    parse(raw) { return ['show', candidateId(raw)] },
  },
  build: {
    description: 'Build one Docker-ready asset candidate',
    hint: '<number-or-id>',
    parse(raw) { return ['build', candidateId(raw)] },
  },
  assets: { description: 'Browse compiled assets', parse: noInput('/assets', ['assets']) },
  runs: { description: 'Browse saved validation runs', parse: noInput('/runs', ['runs']) },
  validate: {
    description: 'Validate one asset in the Docker execution world',
    hint: '<number-or-id>',
    parse(raw) { return ['validate', candidateId(raw)] },
  },
  calibrate: {
    description: 'Self-play an asset 5 times and target 2 verifier passes',
    hint: '<number-or-id>',
    parse(raw) {
      return [
        'calibrate',
        candidateId(raw),
        '--trials',
        '5',
        '--target-passes',
        '2',
        '--allow-upload',
      ]
    },
  },
  harden: {
    description: 'Derive and self-play a harder child task',
    hint: '<verified-number-or-id>',
    parse(raw) {
      return [
        'harden',
        candidateId(raw),
        '--trials',
        '5',
        '--target-passes',
        '2',
        '--allow-upload',
      ]
    },
  },
  evolve: {
    description: 'Explore, compress, and test execution experience',
    hint: '<source> [held-out]',
    parse(raw) {
      const parts = raw.trim().split(/\s+/).filter(Boolean)
      if (parts.length < 1 || parts.length > 2) throw new Error('Usage: /evolve <source> [held-out]')
      return [
        'evolve',
        candidateId(parts[0]),
        ...(parts[1] ? ['--held-out', candidateId(parts[1])] : []),
        '--trials',
        '5',
        '--target-passes',
        '2',
        '--allow-upload',
      ]
    },
  },
  doctor: { description: 'Check local integrations', parse: noInput('/doctor', ['doctor']) },
}

function noInput(usage, args) {
  return (raw) => {
    if (raw.trim()) throw new Error(`Usage: ${usage}`)
    return args
  }
}

export function registerRole(ctx, names, { registerTools = true, registerCommands = true } = {}) {
  if (registerTools) {
    for (const name of names) ctx.tools.register(TOOLS[name]())
  }
  if (!registerCommands) return
  for (const name of names) {
    const command = COMMANDS[name]
    if (!command) continue
    ctx.effect(() => ctx.commands.register({
      name,
      description: command.description,
      ...(command.hint ? { input: { hint: command.hint } } : {}),
      async handler(invocation) {
        try {
          const args = command.parse(invocation.rawInput)
          const text = await requireCoreSuccess(args, { signal: invocation.signal })
          return { kind: 'success', text }
        } catch (error) {
          return { kind: 'error', text: error instanceof Error ? error.message : String(error) }
        }
      },
    }), `evotrace-${name}: command`)
  }
}
