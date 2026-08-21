import { registerRole } from './domain.mjs'

export const name = 'evotrace-builder'
export const inject = ['tools', 'commands']

export function apply(ctx) {
  registerRole(ctx, ['candidates', 'show', 'build', 'harden', 'assets', 'doctor'])
}
