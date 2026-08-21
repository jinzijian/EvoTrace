window.__ModuleLoader__.load({
  id: '@evotrace/dsh-client-brand',
  factory: (require) => {
    const module = { exports: {} }
    const exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })
    const { jsx, jsxs } = require('react/jsx-runtime')

    function EvoTraceMark({ size, className }) {
      return jsxs('svg', {
        width: size,
        height: size,
        viewBox: '0 0 64 64',
        fill: 'none',
        className,
        role: 'img',
        'aria-label': 'EvoTrace',
        children: [
          jsx('path', {
            d: 'M48 14H25C17.3 14 11 20.3 11 28s6.3 14 14 14h14',
            stroke: '#6e40c9',
            strokeWidth: 8,
            strokeLinecap: 'round',
          }),
          jsx('path', {
            d: 'M24 28h25M39 42l10-14-10-14',
            stroke: '#58a6ff',
            strokeWidth: 7,
            strokeLinecap: 'round',
            strokeLinejoin: 'round',
          }),
          jsx('circle', { cx: 24, cy: 42, r: 5, fill: '#3fb950' }),
        ],
      })
    }

    function EvoTraceName() {
      return jsxs('span', {
        style: {
          display: 'inline-flex',
          alignItems: 'baseline',
          letterSpacing: '-0.02em',
          fontSize: '18px',
          fontWeight: 750,
          color: 'var(--dsh-color-text-primary, currentColor)',
        },
        children: [
          'Evo',
          jsx('span', { style: { color: '#58a6ff' }, children: 'Trace' }),
        ],
      })
    }

    const COPY = new Map([
      ['探索未至之境', '把真实轨迹变成可训练资产'],
      ['Explore the unknown', 'Turn lived trajectories into trainable assets'],
      ['Into the Unknown', 'Turn lived trajectories into trainable assets'],
      ['预览版', 'EARLY ALPHA'],
      ['Preview', 'EARLY ALPHA'],
      ['内测声明', 'EvoTrace Early Alpha'],
      ['Internal Testing Notice', 'EvoTrace Early Alpha'],
      ['Add an API key to get started', 'Choose a model provider to get started'],
      ['配置 DeepSeek 官方模型，即可开始使用。', '可先配置 DeepSeek，或稍后在设置中添加 OpenAI / Anthropic 模型。'],
      ['Configure an official DeepSeek model to get started.', 'Configure DeepSeek now, or add OpenAI / Anthropic later in Settings.'],
      ['Configure the official DeepSeek provider to start building.', 'Configure DeepSeek now, or add OpenAI / Anthropic later in Settings.'],
      [
        'DeepSeek Harness 目前的 0.1 版本仍处在面向 Harness 开发者进行测试的阶段，还有许多地方需要持续改进和打磨，希望听取广大开发者的反馈建议。预计 DeepSeek Harness 的核心插件以及基础 API 都会在接下来的一段时间内快速迭代、持续演化。',
        'EvoTrace 基于 DeepSeek Harness 构建，目前仍处于 early alpha。它在本地导入 Claude Code 和 Codex 历史，将 raw trajectory 编译为可训练的数据、可重放的 RL environment 和 execution reward 候选。',
      ],
      [
        '我们期待与全球开发者一起，在开源、开放、可复用、可组合的基础设施之上，共同探索智能上限。欢迎全球 Harness 开发者加入 DSH 插件生态。',
        '轨迹本身不等于训练资产：EvoTrace 保留 provenance、隔离 Builder 与 Validator，并且默认不上传任何本地数据。',
      ],
      [
        'DeepSeek Harness 0.1 remains in testing for Harness developers. Many areas need further improvement, and we welcome feedback from the developer community. DeepSeek Harness\'s core plugins and foundational APIs will continue to evolve rapidly over the coming months.',
        'EvoTrace is built on DeepSeek Harness and remains early alpha. It imports local Claude Code and Codex history, then compiles raw trajectories into trainable data, replayable RL environments, and execution-reward candidates.',
      ],
      [
        'We look forward to exploring the limits of intelligence with developers around the world, building on open-source, open, reusable, and composable infrastructure. We welcome Harness developers everywhere to join the DSH plugin ecosystem.',
        'A trajectory is not yet a training asset: EvoTrace preserves provenance, separates Builder from Validator, and uploads no local data by default.',
      ],
      ['描述你想要构建的内容', '描述你想从轨迹中挖掘或构建的资产'],
      ['Describe what you want to build', 'Describe the trajectory asset you want to mine or build'],
    ])

    function applyProductCopy() {
      if (document.title !== 'EvoTrace') document.title = 'EvoTrace'
      if (!document.body) return
      for (const dialog of document.querySelectorAll('[aria-label="内测声明"], [aria-label="Internal Testing Notice"]')) {
        dialog.setAttribute('aria-label', 'EvoTrace Early Alpha')
      }
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode())) {
        const replacement = COPY.get(node.nodeValue)
        if (replacement) node.nodeValue = replacement
      }
    }

    const inject = ['slots']
    function apply(ctx) {
      applyProductCopy()
      let theme = document.querySelector('meta[name="theme-color"]')
      if (!theme) {
        theme = document.createElement('meta')
        theme.name = 'theme-color'
        document.head.appendChild(theme)
      }
      theme.content = '#0d1117'
      const observer = new MutationObserver(() => queueMicrotask(applyProductCopy))
      observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true })
      ctx.effect(() => {
        const timer = setTimeout(applyProductCopy, 0)
        return () => {
          clearTimeout(timer)
          observer.disconnect()
        }
      }, 'evotrace-brand: product copy')
      ctx.slots.inject('sidebar.brand.mark', () =>
        ctx.slots.inject('sidebar.brand.name', () =>
          ctx.slots.inject('conversation.hero.brand.mark', function* () {
            yield ctx.slots.register({ name: 'sidebar.brand.mark' }, EvoTraceMark)
            yield ctx.slots.register({ name: 'sidebar.brand.name' }, EvoTraceName)
            yield ctx.slots.register({ name: 'conversation.hero.brand.mark' }, EvoTraceMark)
          })))
    }

    exports.apply = apply
    exports.inject = inject
    return module.exports
  },
})
