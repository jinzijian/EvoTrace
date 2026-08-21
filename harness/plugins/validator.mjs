import { registerRole } from './domain.mjs'

export const name = 'evotrace-validator'
export const inject = ['tools', 'commands']

export function apply(ctx) {
  registerRole(ctx, ['show', 'assets', 'validate', 'calibrate', 'evolve', 'runs', 'doctor'])
}
