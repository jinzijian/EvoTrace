import { registerRole } from './domain.mjs'

export const name = 'evotrace-curator'
export const inject = ['tools', 'commands']

export function apply(ctx) {
  registerRole(ctx, ['init', 'import', 'mine', 'candidates', 'search', 'show', 'doctor'])
}
