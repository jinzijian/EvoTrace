<div align="center">

# ScaleVerifier

### 把每一条 Claude Code 和 Codex session，变成可复用的训练、评测与验证资产。

[English](README.md)

**Agent session 不是用完即弃的聊天记录，而是会持续复利的数据资产。**

</div>

每一条 Claude Code 和 Codex session 都不只是聊天历史：里面包含 task intent、human preference、执行证据、
恢复过程和 verifier signal。ScaleVerifier 把这些原本会流失的轨迹转化成可复用的训练、评测与验证资产，
同时不要求用户更换 coding agent，也不要求请求先经过一个代理层。

```text
Claude Code / Codex history
            +
       本地 Git 考古
            ↓
      统一 trajectory
            ↓
       本地 curator
       ┌────┴─────┐
       ↓          ↓
 preference    executable
 DPO / SFT     task + env
 QA / pairs    Docker + verifier
```

> [!WARNING]
> ScaleVerifier 目前是 early alpha。分类标签和自动生成的 verifier 都是证据，不是任务质量或语义正确性的
> 证明。用于重要评测或分享之前必须人工检查。

## 第一天的 magic moment

```console
$ vf import
Found 184 session(s)
Discovered files             186
Indexed files                186
...

$ vf mine
Found                         184
Useful                        73
Human corrected               31
Execution-verifiable          26
Preference candidates         19
Recovery trajectories         14
Low-value / trivial           111

$ vf build
SESSION                                  STATUS          BUNDLE / REASON
codex-...                                built           ~/.scaleverifier/benchmarks/codex-...
```

这里的数字只是 UX 示例；真实运行只会展示你本地历史产生的统计。V0.2 curator 是确定性的可审计 heuristic，
不会调用 LLM，也不会上传 session 数据。

## 安装

```bash
uv tool install git+https://github.com/jinzijian/scaleverifier.git
vf doctor
```

本地开发：

```bash
git clone https://github.com/jinzijian/scaleverifier.git
cd scaleverifier
uv sync
uv run vf doctor
```

项目没有第三方 Python runtime dependency。Git 是必须的；Docker 用于后续隔离执行。`scaleverifier` 和
`sv` 继续作为 `vf` 的兼容别名。

## 三个核心命令

### `vf import`：导入已经存在的历史

```bash
# 自动发现两个来源，导入当前可见的全部 session
vf import

# 只导入某一个来源，或限制最近文件数
vf import codex
vf import claude --last 20

# 也可以给出精确文件
vf import codex ~/.codex/sessions/2026/08/19/rollout-*.jsonl
vf import codex ~/.codex/history.jsonl
vf import claude ~/.claude/projects/my-project/session.jsonl
```

Importer 遵循 `$CODEX_HOME` 和 `$CLAUDE_CONFIG_DIR`。对于 Codex，它会区分包含完整事件的 session JSONL
与较轻的 prompt history；同一个 session 两者都存在时保留信息更丰富的一份。Claude Code 文档说明
session transcript 以明文保存在 `~/.claude/projects/`，默认清理窗口是 30 天，因此安装后尽快做一次索引
很重要。参考 [Claude Code session 文档](https://code.claude.com/docs/en/sessions)、
[Claude Code 数据目录文档](https://code.claude.com/docs/en/claude-directory)、
[Codex 配置文档](https://developers.openai.com/codex/config-reference) 和
[Codex CLI resume 文档](https://developers.openai.com/codex/cli/reference)。

Import 默认是增量的：用文件大小和修改时间跳过没有变化的源文件。`--refresh` 可以强制重建索引。原始
history 只在原位置读取，不会被复制进 ScaleVerifier store。

### `vf mine`：找到值得保留的 experience

```bash
vf mine
vf mine --source codex --min-score 4
vf mine --json
```

V0.2 只依赖能观测到的证据：是否恢复出非平凡任务、是否调用代码编辑工具、是否运行 test/lint/build、失败
之后是否成功、agent 工作后是否出现人工纠正，以及仓库 base 的恢复置信度。每个 candidate 的 score、label、
signal 和 evidence 都保存在 `~/.scaleverifier/candidates/`。

- `preference_candidate`：可能形成 rejected/chosen、纠错 pair、DPO、QA 或 SFT 数据；
- `execution_verifiable`：有代码编辑、验证命令和可恢复的 repository base；
- `recovery_trajectory`：失败或人工纠正之后继续修复并出现恢复工作。

第一版故意不使用 model judge。以后可以把 curator agent 接到相同 schema 后面，但不能抹掉 provenance。

### `vf build`：编译可执行 eval 资产

```bash
# 编译得分最高的 execution-verifiable candidates
vf build --limit 10

# 编译一个 session，并显式补充可信 verifier
vf build SESSION_ID \
  --verify "python -m pytest tests/integration -q" \
  --verify "python -m ruff check src"
```

Transcript 本身不等于完整 environment。Builder 会把 session evidence 与本地 Git archaeology 合并：优先
使用 session 记录的 commit，否则尝试按时间查找 commit，并明确记录 reconstruction confidence，而不是假装
恢复结果完全准确。产物包括：

```text
benchmark-id/
├── task.md
├── task.json
├── task.yaml
├── verifier.py
├── verifier.json
├── sandbox-policy.json
├── setup.sh
├── Dockerfile
├── environment/
│   ├── base.tar.gz
│   ├── environment.json
│   └── untracked-initial.tar.gz
└── patches/
    ├── initial.patch
    └── reference.patch
```

Verifier 的来源始终可见：用户显式提供、trajectory 中恢复、repository convention 推断，或者明确警告当前
没有 behavioral verifier。

## 第二天以后：持续增量 capture

```bash
vf watch                 # 每五分钟检查一次，并重新 mine
vf watch --interval 60
vf watch --once          # 适合 cron / nightly job
```

Watcher 只读取发生变化的 history 文件，不修改 Claude Code、Codex 或任何源代码仓库。

## Agent 只能在容器里工作

宿主侧 importer/builder 可以读取 session 文件和 Git object，但绝不能把可写的宿主 checkout 交给 autonomous
agent。每个 bundle 都包含 `sandbox-policy.json` 和非 root Dockerfile。硬边界是：

- 源代码以 archive 复制进 image，而不是可写 bind mount；
- 不挂 Docker socket，不使用 privileged/host PID，不传宿主 credentials；
- runtime 默认断网、drop Linux capabilities、禁止 new privileges；
- agent 可以随意修改或删除容器里的 `/workspace` 副本；
- 只有验证后的结果才能被提升到一个全新、唯一的 host run directory；
- 内部系统只能通过显式只读 adapter 或 deterministic mock 暴露，绝不提供 production write credential。

因此 V0.2 已经拒绝旧的 `benchmark --agent` 宿主执行方式。安全的 container orchestrator 是下一个 runtime
milestone；当前 `vf build` 会生成其完整且可检查的输入。已有 candidate checkout 仍可由用户显式执行
`vf benchmark ... --candidate NAME=PATH` 评分。

完整约束见 [sandbox contract](docs/sandbox-contract.md) 和 [SECURITY.md](SECURITY.md)。

## 存储与隐私

默认 store 是 `~/.scaleverifier/`，也可以用 `$SCALEVERIFIER_HOME` 或 `--home` 修改。

- 不需要账号、hosted LLM、API key、遥测、付费流程或数据上传；
- 统一后的文本会进行 best-effort secret redaction；
- untracked snapshot 排除 Git-ignored、常见 `.env` 和私钥后缀；
- 编译 bundle 包含源码，仍可能带有 Git 已跟踪的 secret，检查之前必须把它当作私有资产。

## V0.2 已实现

- Claude Code / Codex 全量与增量历史发现；
- rich session 与 prompt history 的去重优先级；
- 本地统一、脱敏 trajectory；
- preference、execution、correction、recovery、low-value 的可审计 mining；
- Git 时间对齐与 reconstruction confidence；
- task、environment、Dockerfile、verifier bundle；
- container-only policy、非 root image、禁止 host-agent fallback；
- V0.1 的 replay、verifier 与已有 candidate 评分。

下一步：

- 只在 sandbox 中运行的 curator/builder agent，自动生成 mock 并补全 verifier；
- verifier validation 与 reward-hacking 检查；
- opt-in 只读 internal adapter 和 record/replay mock；
- DPO、preference、QA、SFT、executable eval 导出；
- 去重、难度估计和 benchmark registry。

数据结构见 [docs/design.md](docs/design.md) 和 [docs/schema.md](docs/schema.md)。

## License

Apache License 2.0。
