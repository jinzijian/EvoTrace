#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer } from 'node:net'

const url = process.argv[2] || 'http://127.0.0.1:5678/'
const output = resolve(process.argv[3] || join(process.cwd(), 'assets', '.harness-demo-frames'))
const chrome = process.env.EVOTRACE_DEMO_CHROME
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const language = process.env.EVOTRACE_DEMO_LANG || 'en-US'

const delay = milliseconds => new Promise(resolveDelay => setTimeout(resolveDelay, milliseconds))

async function freePort() {
  return await new Promise((resolvePort, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      server.close(() => resolvePort(address.port))
    })
  })
}

async function pageTarget(port) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json())
      const page = targets.find(target => target.type === 'page' && target.url.startsWith('http'))
      if (page?.webSocketDebuggerUrl) return page
    } catch {
      // Chrome may still be starting.
    }
    await delay(100)
  }
  throw new Error('Chrome DevTools page did not become ready')
}

function protocol(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl)
  let sequence = 0
  const pending = new Map()

  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (!message.id) return
    const request = pending.get(message.id)
    if (!request) return
    pending.delete(message.id)
    if (message.error) request.reject(new Error(message.error.message))
    else request.resolve(message.result)
  })

  const ready = new Promise((resolveReady, reject) => {
    socket.addEventListener('open', resolveReady, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })

  return {
    ready,
    close: () => socket.close(),
    async send(method, params = {}) {
      await ready
      const id = ++sequence
      return await new Promise((resolveRequest, reject) => {
        pending.set(id, { resolve: resolveRequest, reject })
        socket.send(JSON.stringify({ id, method, params }))
      })
    },
  }
}

async function clickText(cdp, labels) {
  const expression = `(() => {
    const labels = ${JSON.stringify(labels)};
    const button = Array.from(document.querySelectorAll('button')).find(candidate =>
      labels.some(label => candidate.textContent?.includes(label))
    );
    if (!button) return false;
    button.click();
    return true;
  })()`
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true })
  return result.result.value === true
}

async function capture(cdp, filename) {
  const result = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  writeFileSync(join(output, filename), Buffer.from(result.data, 'base64'))
}

mkdirSync(output, { recursive: true })
const profile = mkdtempSync(join(tmpdir(), 'evotrace-demo-chrome-'))
const port = await freePort()
const processHandle = spawn(chrome, [
  '--headless=new',
  '--disable-gpu',
  '--hide-scrollbars',
  '--no-first-run',
  '--no-default-browser-check',
  `--lang=${language}`,
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`,
  '--window-size=1440,900',
  url,
], { stdio: 'ignore' })

let cdp
try {
  const target = await pageTarget(port)
  cdp = protocol(target.webSocketDebuggerUrl)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  await cdp.send('Network.setExtraHTTPHeaders', {
    headers: { 'Accept-Language': `${language},en;q=0.9` },
  })
  await cdp.send('Emulation.setLocaleOverride', { locale: language })
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
    source: `
      Object.defineProperty(navigator, 'language', { get: () => ${JSON.stringify(language)} });
      Object.defineProperty(navigator, 'languages', { get: () => [${JSON.stringify(language)}, 'en'] });
    `,
  })
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await cdp.send('Page.reload', { ignoreCache: true })
  await delay(2200)
  await capture(cdp, '01-provider.png')

  if (!await clickText(cdp, ['稍后配置', 'Configure later'])) {
    throw new Error('could not find the configure-later onboarding button')
  }
  await delay(1200)
  await capture(cdp, '02-home.png')

  if (!await clickText(cdp, ['EvoTrace Curator'])) {
    throw new Error('could not find the EvoTrace Curator preset button')
  }
  await delay(500)
  await capture(cdp, '03-roles.png')
  process.stdout.write(`${output}\n`)
} finally {
  cdp?.close()
  processHandle.kill('SIGTERM')
  await Promise.race([once(processHandle, 'exit'), delay(2000)])
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
