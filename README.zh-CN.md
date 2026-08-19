<div align="center">

# ScaleVerifier

### 把真实 coding-agent 使用轨迹变成可执行 eval。

[English](README.md)

**继续使用你原来的 coding agent。ScaleVerifier 把它已经做过的真实工作，编译成评测下一个 agent 所需的 task、environment 和 verifier。**

</div>

---

你的团队其实每天都在产生最相关的 coding-agent benchmark：真实工作。

问题是，这些工作散落在 agent history、本地仓库、shell 输出和人工纠正中。ScaleVerifier 把它们编译成
可携带、可恢复、可执行的 eval bundle：

```text
(initial state, task, trajectory, final state, verifier)
```

它不是另一个 coding agent，也不是另一个通用 harness 或 trace dashboard。它负责解决更上游的问题：

> 真实 benchmark 从哪里来？

> [!WARNING]
> ScaleVerifier 目前是 early alpha。CLI 和 bundle schema 可能变化。自动生成的 verifier 是正确性的证据，
> 不是语义正确性的证明；请在高风险场景中人工检查。

## 安装

```bash
uv tool install git+https://github.com/jinzijian/scaleverifier.git
```

本地开发：

```bash
git clone https://github.com/jinzijian/scaleverifier.git
cd scaleverifier
uv sync
uv run scaleverifier doctor
```

运行时没有第三方 Python 依赖。必须安装 Git，Docker 可选。

## 五分钟上手

### 1. 导入已有历史

```bash
# 自动发现最近 20 个本地 session
scaleverifier import codex --last 20
scaleverifier import claude --last 20

# 也可以指定文件
scaleverifier import codex ~/.codex/sessions/2026/08/19/rollout-*.jsonl
scaleverifier import claude ~/.claude/projects/my-project/session.jsonl

scaleverifier sessions
```

所有导入都在本地完成。ScaleVerifier 会保存脱敏后的统一事件，不会复制原始历史文件。

### 2. 记录一次新任务

```bash
scaleverifier record \
  --task "增加 cursor pagination，同时保持旧 API 兼容" \
  --verify "python -m pytest -q" \
  -- claude
```

可以包住 Codex、Claude Code、其他 CLI agent 或内部 agent。通用 recorder 能看到进程输出和 Git 状态；
原生 history importer 可以恢复更丰富的 tool-call 事件。

### 3. 编译成 benchmark

```bash
scaleverifier compile latest

# 自动推断不够时，显式补充 verifier
scaleverifier compile SESSION_ID \
  --verify "python -m pytest tests/integration -q" \
  --verify "python -m ruff check src"
```

Verifier 的来源优先级是：

1. `record` 或 `compile` 时显式提供的命令；
2. 从 trajectory 中恢复出的 test/build/lint/typecheck 命令；
3. 根据仓库结构保守推断的 `pytest`、`npm test`、`cargo test`、`go test` 等命令。

如果无法恢复行为 verifier，ScaleVerifier 会明确警告，只生成较弱的“仓库确实发生修改”检查，不会把弱信号
伪装成强 verifier。

### 4. 恢复初始环境

```bash
scaleverifier replay latest --dest /tmp/scaleverifier-task
```

Replay 会恢复 base tree、任务开始前已有的 tracked patch 和允许的 untracked 文件，不会应用 reference
solution。

### 5. 运行评测

```bash
scaleverifier benchmark latest \
  --agent 'codex=codex exec "$SCALEVERIFIER_TASK"' \
  --agent 'claude=claude -p "$SCALEVERIFIER_TASK"'
```

也可以直接评测已经存在的 checkout：

```bash
scaleverifier benchmark latest \
  --candidate candidate-a=/path/to/checkout-a \
  --candidate candidate-b=/path/to/checkout-b
```

## 编译产物

```text
benchmark-id/
├── task.md
├── task.json
├── task.yaml
├── verifier.py
├── verifier.json
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

`reference.patch` 可以用于错误分析和保守的 verifier synthesis，但评测候选方案时绝不会应用它，也不会要求
候选方案与 reference patch 完全一致。

## 隐私边界

ScaleVerifier 不需要账号、服务端或遥测接口：

- 默认数据保存在当前 Git 仓库的 `.scaleverifier/`，也可用 `$SCALEVERIFIER_HOME` 指定；
- 不复制 Codex/Claude Code 的原始历史；
- 对统一事件中的常见 token 和 secret 做 best-effort 脱敏；
- untracked snapshot 排除 Git-ignored 文件、常见 `.env` 和私钥文件；
- 这个开源项目没有任何自动上传、trajectory 授权或 marketplace 行为。

但是，为了可复现，编译 bundle 会包含 base source tree 和 patch。已经被 Git 跟踪的秘密或嵌入代码的凭证仍
可能存在。**分享前必须把每个 bundle 当作私有数据检查。** 详见 [SECURITY.md](SECURITY.md)。

## 当前实现

- Codex 与 Claude Code 本地历史导入；
- 通用 PTY/process recorder；
- 统一 trajectory schema 与 best-effort redaction；
- Git base state、初始 dirty patch、untracked 文件快照；
- portable replay bundle 与自动生成 Dockerfile；
- 显式、trajectory 推断、repo 推断三类 verifier；
- fresh-workspace agent benchmark 与现有 checkout 评分；
- 基础 failure-signal mining；
- 防止 test-generated cache 造成 false pass，并保护 reference 没有修改过的已有测试文件。

更完整的设计与 schema 见 [docs/design.md](docs/design.md) 和 [docs/schema.md](docs/schema.md)。

## License

Apache License 2.0。
