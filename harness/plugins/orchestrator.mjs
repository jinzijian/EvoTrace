import { randomUUID } from 'node:crypto'

import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { defineTool } from '@deepseek-ai/dsh-tools'

import { candidateId, requireCoreSuccess } from './core-runner.mjs'
import { registerRole } from './domain.mjs'

export const name = 'evotrace-orchestrator'
export const inject = ['tools', 'commands']

const REVIEW_ROUTES = ['direct', 'derived_seed', 'preference_only', 'reject']
const reviewStates = new Map()

const textOutput = {
  schema: { type: 'string' },
  render: (_args, value) => [{ type: 'text', text: value }],
}

function reviewState(token, candidate) {
  const state = reviewStates.get(String(token ?? ''))
  if (!state) throw new Error('review token is missing, expired, or does not belong to this review')
  const normalized = candidateId(candidate)
  if (state.candidate !== normalized) {
    throw new Error(`review lineage violation: expected ${state.candidate}, received ${normalized}`)
  }
  return state
}

function mutationState(token, candidate, route) {
  const state = reviewState(token, candidate)
  if (!state.route) throw new Error('review route has not been recorded by Candidate Gate')
  if (state.route !== route) {
    throw new Error(`review route ${state.route} forbids ${route === 'direct' ? 'build' : 'hardening'}`)
  }
  return state
}

function producedAssetState(token, candidate) {
  const state = reviewStates.get(String(token ?? ''))
  if (!state) throw new Error('review token is missing, expired, or does not belong to this review')
  const normalized = candidateId(candidate)
  if (!state.assetCandidate || state.assetCandidate !== normalized) {
    throw new Error(`review lineage violation: no asset ${normalized} was produced by this review`)
  }
  return state
}

function registerReviewTools(ctx) {
  ctx.tools.register(defineTool({
    name: 'review_route',
    description: 'Record the Candidate Gate route for one review token and lock every downstream mutation to that candidate lineage.',
    parameters: {
      reviewToken: { type: 'string', required: true },
      candidate: { type: 'string', required: true },
      route: { type: 'string', enum: REVIEW_ROUTES, required: true },
      rationale: { type: 'string', required: true },
    },
    output: textOutput,
    execute: ({ reviewToken, candidate, route, rationale }) => {
      const state = reviewState(reviewToken, candidate)
      if (state.route) throw new Error(`review route is immutable: ${state.route} was already recorded`)
      state.route = route
      state.rationale = String(rationale).slice(0, 4000)
      return JSON.stringify({ reviewToken, candidate: state.candidate, route: state.route })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'review_asset_build',
    description: 'Build only the exact direct candidate bound to a recorded review route.',
    parameters: {
      reviewToken: { type: 'string', required: true },
      candidate: { type: 'string', required: true },
    },
    output: textOutput,
    async execute({ reviewToken, candidate }, exec) {
      const state = mutationState(reviewToken, candidate, 'direct')
      const output = await requireCoreSuccess(['build', state.candidate, '--json'], { signal: exec.signal })
      state.assetCandidate = state.candidate
      return output
    },
  }))

  ctx.tools.register(defineTool({
    name: 'review_asset_harden',
    description: 'Harden only the exact verified seed bound to a derived_seed review route.',
    parameters: {
      reviewToken: { type: 'string', required: true },
      candidate: { type: 'string', required: true },
      trials: { type: 'integer' },
      targetPasses: { type: 'integer' },
      timeout: { type: 'integer' },
    },
    output: textOutput,
    async execute({ reviewToken, candidate, trials = 5, targetPasses = 2, timeout = 600 }, exec) {
      const state = mutationState(reviewToken, candidate, 'derived_seed')
      if (trials < 1 || trials > 20) throw new Error('trials must be between 1 and 20')
      if (targetPasses < 0 || targetPasses > trials) throw new Error('targetPasses must be between 0 and trials')
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      const output = await requireCoreSuccess([
        'harden', state.candidate,
        '--trials', String(trials),
        '--target-passes', String(targetPasses),
        '--timeout', String(timeout),
        '--allow-upload',
        '--json',
      ], { signal: exec.signal })
      try {
        const report = JSON.parse(output)
        state.assetCandidate = candidateId(report.child_id)
      } catch {
        throw new Error('hardening completed without a machine-readable child lineage')
      }
      return output
    },
  }))

  ctx.tools.register(defineTool({
    name: 'review_asset_validate',
    description: 'Validate only the asset produced by this review token.',
    parameters: {
      reviewToken: { type: 'string', required: true },
      candidate: { type: 'string', required: true },
      timeout: { type: 'integer' },
    },
    output: textOutput,
    async execute({ reviewToken, candidate, timeout = 900 }, exec) {
      producedAssetState(reviewToken, candidate)
      const normalized = candidateId(candidate)
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      return requireCoreSuccess(['validate', normalized, '--timeout', String(timeout), '--json'], { signal: exec.signal })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'review_asset_calibrate',
    description: 'Self-play only the asset produced by this review token.',
    parameters: {
      reviewToken: { type: 'string', required: true },
      candidate: { type: 'string', required: true },
      trials: { type: 'integer' },
      targetPasses: { type: 'integer' },
      timeout: { type: 'integer' },
    },
    output: textOutput,
    async execute({ reviewToken, candidate, trials = 5, targetPasses = 2, timeout = 600 }, exec) {
      producedAssetState(reviewToken, candidate)
      const normalized = candidateId(candidate)
      if (trials < 1 || trials > 20) throw new Error('trials must be between 1 and 20')
      if (targetPasses < 0 || targetPasses > trials) throw new Error('targetPasses must be between 0 and trials')
      if (timeout < 1 || timeout > 3600) throw new Error('timeout must be between 1 and 3600')
      return requireCoreSuccess([
        'calibrate', normalized,
        '--trials', String(trials),
        '--target-passes', String(targetPasses),
        '--timeout', String(timeout),
        '--allow-upload',
        '--json',
      ], { signal: exec.signal })
    },
  }))
}

export function createReviewCommand({
  preflightCandidate = (candidate, signal) => requireCoreSuccess(
    ['show', candidate, '--json'],
    { signal },
  ),
} = {}) {
  return {
    name: 'review',
    description: 'Run the sequential four-agent review pipeline for one trajectory',
    input: { hint: '<number-or-id>' },
    async handler(invocation) {
      let candidate
      try {
        candidate = candidateId(invocation.rawInput)
        await preflightCandidate(candidate, invocation.signal)
      } catch (error) {
        return { kind: 'error', text: error instanceof Error ? error.message : String(error) }
      }
      const reviewToken = randomUUID()
      reviewStates.set(reviewToken, {
        candidate,
        route: null,
        assetCandidate: null,
        createdAt: Date.now(),
      })
      invocation.agent.steer(createUserMessage({
        content: [{
          type: 'text',
          text: [
            `Review EvoTrace trajectory candidate ${candidate}.`,
            `Review token: ${reviewToken}. Pass this exact token and candidate id to every review-bound tool.`,
            'Run the mandatory sequential pipeline now:',
            'Episode Miner -> Candidate Gate -> Task Builder/Hardener -> Verifier Critic.',
            'Wait for every foreground subagent result, pass each complete report downstream,',
            'then return one evidence-backed classification and exact next action.',
          ].join(' '),
        }],
        source: { kind: 'user' },
      }))
      return {
        kind: 'success',
        text: `Started sequential four-agent review for ${candidate} (${reviewToken}).`,
      }
    },
  }
}

export function apply(ctx) {
  registerRole(ctx, [
    'init',
    'import',
    'mine',
    'candidates',
    'search',
    'show',
    'assets',
    'runs',
    'doctor',
  ])
  registerRole(ctx, ['build', 'harden', 'validate', 'calibrate', 'evolve'], {
    registerTools: false,
  })
  registerReviewTools(ctx)
  ctx.effect(
    () => ctx.commands.register(createReviewCommand()),
    'evotrace-review: command',
  )
}
