import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { delimiter, join } from 'node:path'

const OUTPUT_LIMIT = 512 * 1024

function projectRoot() {
  const root = process.env.EVOTRACE_CORE_ROOT
  if (!root) throw new Error('EVOTRACE_CORE_ROOT is not configured')
  return root
}

function pythonExecutable() {
  const configured = process.env.EVOTRACE_CORE_PYTHON
  if (configured) return configured
  const local = join(projectRoot(), '.venv', 'bin', 'python')
  return existsSync(local) ? local : 'python3'
}

function appendBounded(chunks, chunk, state) {
  if (state.bytes >= OUTPUT_LIMIT) return
  const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
  const remaining = OUTPUT_LIMIT - state.bytes
  chunks.push(buffer.subarray(0, remaining))
  state.bytes += Math.min(buffer.length, remaining)
  if (buffer.length > remaining) state.truncated = true
}

/** Run one allowlisted EvoTrace core command without a shell. */
export function runCore(args, { signal } = {}) {
  const root = projectRoot()
  const store = process.env.EVOTRACE_STORE_HOME ?? process.env.EVOTRACE_HOME
  const pythonPath = [join(root, 'src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter)
  const command = [
    '-c',
    'from evotrace.cli import main; raise SystemExit(main())',
    ...(store ? ['--home', store] : []),
    ...args,
  ]

  return new Promise((resolve, reject) => {
    const child = spawn(pythonExecutable(), command, {
      cwd: root,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        EVOTRACE_SLASH_MODE: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: false,
    })
    const stdout = []
    const stderr = []
    const stdoutState = { bytes: 0, truncated: false }
    const stderrState = { bytes: 0, truncated: false }
    child.stdout.on('data', chunk => appendBounded(stdout, chunk, stdoutState))
    child.stderr.on('data', chunk => appendBounded(stderr, chunk, stderrState))

    const abort = () => child.kill('SIGTERM')
    if (signal?.aborted) abort()
    else signal?.addEventListener('abort', abort, { once: true })

    child.once('error', reject)
    child.once('close', (code, terminatedBy) => {
      signal?.removeEventListener('abort', abort)
      if (signal?.aborted) {
        reject(signal.reason instanceof Error ? signal.reason : new Error('EvoTrace operation aborted'))
        return
      }
      const out = Buffer.concat(stdout).toString('utf8').trim()
      const err = Buffer.concat(stderr).toString('utf8').trim()
      const truncation = stdoutState.truncated || stderrState.truncated
        ? '\n[output truncated by EvoTrace Harness]'
        : ''
      const text = [out, err].filter(Boolean).join('\n') + truncation
      resolve({ code: code ?? 1, signal: terminatedBy, text: text || '(no output)' })
    })
  })
}

export async function requireCoreSuccess(args, options) {
  const result = await runCore(args, options)
  if (result.code !== 0) {
    throw new Error(result.text || `EvoTrace core exited with status ${result.code}`)
  }
  return result.text
}

export function candidateId(value) {
  const normalized = String(value ?? '').trim()
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/u.test(normalized)) {
    throw new Error('candidate must be a number, session id, or id prefix without spaces')
  }
  return normalized
}

export function queryText(value) {
  const normalized = String(value ?? '').trim()
  if (!normalized || normalized.startsWith('-')) {
    throw new Error('query must be non-empty and cannot start with a flag')
  }
  if (normalized.length > 1000) throw new Error('query is too long')
  return normalized
}
