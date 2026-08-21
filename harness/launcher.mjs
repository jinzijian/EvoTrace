#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process'
import {
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readlinkSync,
  realpathSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HARNESS_DIR = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(HARNESS_DIR, '..')
const VERSION = JSON.parse(readFileSync(join(PROJECT_ROOT, 'package.json'), 'utf8')).version
const PRESET_IDS = ['evotrace-orchestrator']

export function resolveHomes(env = process.env) {
  const storeHome = resolve(env.EVOTRACE_HOME || join(homedir(), '.evotrace'))
  const dshHome = resolve(env.EVOTRACE_DSH_HOME || join(storeHome, 'harness'))
  return { storeHome, dshHome }
}

export function ensurePresets(dshHome) {
  const destinationRoot = join(dshHome, '.agent-presets')
  for (const id of PRESET_IDS) {
    const source = join(HARNESS_DIR, 'presets', id)
    const destination = join(destinationRoot, id)
    mkdirSync(destination, { recursive: true, mode: 0o700 })
    const role = id.slice('evotrace-'.length)
    const placeholder = `__EVOTRACE_${role.toUpperCase()}_PLUGIN__`
    const composition = readFileSync(join(source, 'agent.cordis.yml'), 'utf8')
      .replace(placeholder, JSON.stringify(join(HARNESS_DIR, 'plugins', `${role}.mjs`)))
    if (composition.includes(placeholder)) throw new Error(`failed to resolve ${placeholder}`)
    writeFileSync(join(destination, 'agent.cordis.yml'), composition, { mode: 0o600 })
    copyFileSync(join(source, 'preset.yml'), join(destination, 'preset.yml'))
  }
  return destinationRoot
}

function isManagedClientPackage(path) {
  try {
    const packageJson = JSON.parse(readFileSync(join(path, 'package.json'), 'utf8'))
    return packageJson.name === '@evotrace/dsh-client-brand'
  } catch {
    return false
  }
}

export function ensureClientPackage(dshHome, source = join(HARNESS_DIR, 'client-brand')) {
  const scope = join(dshHome, 'profiles', 'node_modules', '@evotrace')
  const destination = join(scope, 'dsh-client-brand')
  mkdirSync(scope, { recursive: true, mode: 0o700 })
  let destinationStat
  try {
    destinationStat = lstatSync(destination)
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error
  }
  if (destinationStat) {
    const stat = lstatSync(destination)
    const currentSource = stat.isSymbolicLink()
      ? resolve(scope, readlinkSync(destination))
      : undefined
    if (!currentSource || !isManagedClientPackage(currentSource)) {
      throw new Error(`${destination} exists but is not the EvoTrace-managed client package link`)
    }
    if (currentSource === source) return destination
    unlinkSync(destination)
  }
  symlinkSync(source, destination, process.platform === 'win32' ? 'junction' : 'dir')
  return destination
}

function pythonExecutable(env = process.env) {
  if (env.EVOTRACE_CORE_PYTHON) return env.EVOTRACE_CORE_PYTHON
  const local = process.platform === 'win32'
    ? join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
    : join(PROJECT_ROOT, '.venv', 'bin', 'python')
  return existsSync(local) ? local : 'python3'
}

export function nodeVersionSupported(version = process.versions.node) {
  const [major = 0, minor = 0] = version.split('.').map(Number)
  return major >= 24 || (major === 22 && minor >= 19)
}

function dshExecutable() {
  const suffix = process.platform === 'win32' ? 'dsh.cmd' : 'dsh'
  return join(PROJECT_ROOT, 'node_modules', '.bin', suffix)
}

export function harnessEnvironment(env = process.env) {
  const { storeHome, dshHome } = resolveHomes(env)
  return {
    ...env,
    DSH_HOME: dshHome,
    DSH_PERMISSION_MODE: env.DSH_PERMISSION_MODE || 'danger-full-access',
    DSH_TELEMETRY_DISABLED: env.DSH_TELEMETRY_DISABLED || '1',
    EVOTRACE_HOME: storeHome,
    EVOTRACE_STORE_HOME: storeHome,
    EVOTRACE_CORE_ROOT: PROJECT_ROOT,
    EVOTRACE_CORE_PYTHON: pythonExecutable(env),
    EVOTRACE_PRESET_ROOT: join(dshHome, '.agent-presets'),
  }
}

function printHelp() {
  process.stdout.write(`EvoTrace — turn real-world coding-agent trajectories into post-training assets.\n\n`)
  process.stdout.write(`Usage:\n`)
  process.stdout.write(`  evotrace                 Launch the EvoTrace Harness UI\n`)
  process.stdout.write(`  evotrace setup           Install the managed sequential Orchestrator preset\n`)
  process.stdout.write(`  evotrace doctor          Check the Harness and local compiler runtime\n`)
  process.stdout.write(`  evotrace --help          Show this help\n\n`)
  process.stdout.write(`Inside the app, type / to discover EvoTrace commands. Model providers and API keys\n`)
  process.stdout.write(`are configured in Settings; credentials are handled by DeepSeek Harness.\n`)
}

function report(label, ok, detail) {
  const mark = ok ? '✓' : '✗'
  process.stdout.write(`${mark} ${label}: ${detail}\n`)
  return ok
}

export function doctor(env = harnessEnvironment()) {
  const { dshHome } = resolveHomes(env)
  ensurePresets(dshHome)
  ensureClientPackage(dshHome)
  let healthy = true
  healthy = report('Node.js', nodeVersionSupported(), process.version) && healthy
  const dsh = dshExecutable()
  healthy = report('DeepSeek Harness', existsSync(dsh), existsSync(dsh) ? dsh : 'run pnpm install') && healthy
  const python = pythonExecutable(env)
  const pythonCheck = spawnSync(python, ['--version'], { encoding: 'utf8' })
  healthy = report('EvoTrace core Python', pythonCheck.status === 0, (pythonCheck.stdout || pythonCheck.stderr || python).trim()) && healthy
  const coreCheck = spawnSync(python, ['-c', 'import evotrace; print(evotrace.__version__)'], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8',
    env: { ...env, PYTHONPATH: join(PROJECT_ROOT, 'src') },
  })
  healthy = report('Trajectory compiler', coreCheck.status === 0, coreCheck.status === 0 ? `EvoTrace ${coreCheck.stdout.trim()}` : 'Python package unavailable') && healthy
  const dockerCheck = spawnSync('docker', ['version', '--format', '{{.Server.Version}}'], { encoding: 'utf8' })
  report('Docker execution world', dockerCheck.status === 0, dockerCheck.status === 0 ? dockerCheck.stdout.trim() : 'not available; build remains candidate-only')
  report('Managed presets', true, join(dshHome, '.agent-presets'))
  return healthy ? 0 : 1
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.includes('--version') || argv.includes('-V')) {
    process.stdout.write(`EvoTrace ${VERSION}\n`)
    return 0
  }
  if (argv.includes('--help') || argv.includes('-h')) {
    printHelp()
    return 0
  }
  const env = harnessEnvironment()
  const { dshHome } = resolveHomes(env)
  const presets = ensurePresets(dshHome)
  ensureClientPackage(dshHome)
  if (argv[0] === 'setup') {
    process.stdout.write(`EvoTrace Harness presets installed at ${presets}\n`)
    return 0
  }
  if (argv[0] === 'doctor') return doctor(env)

  const dsh = dshExecutable()
  if (!existsSync(dsh)) {
    process.stderr.write('EvoTrace Harness is not installed. Run `pnpm install` in the project directory.\n')
    return 1
  }
  const childArgs = ['web', '--patch', join(HARNESS_DIR, 'evotrace.patch.yml'), ...argv]
  return await new Promise((resolveExit, reject) => {
    const child = spawn(dsh, childArgs, {
      cwd: process.cwd(),
      env,
      stdio: 'inherit',
      shell: process.platform === 'win32',
    })
    child.once('error', reject)
    child.once('exit', (code, signal) => {
      if (signal) process.kill(process.pid, signal)
      resolveExit(code ?? 1)
    })
  })
}

export function invokedAsMain(argvPath = process.argv[1]) {
  if (!argvPath) return false
  try {
    return realpathSync(resolve(argvPath)) === realpathSync(fileURLToPath(import.meta.url))
  } catch {
    return false
  }
}

const isMain = invokedAsMain()
if (isMain) {
  try {
    process.exitCode = await main()
  } catch (error) {
    process.stderr.write(`EvoTrace failed: ${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  }
}
