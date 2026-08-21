<div align="center">

<img src="assets/evotrace-logo.svg" alt="EvoTrace logo" width="112">

# EvoTrace

### 把真实的 Claude Code 和 Codex session 变成可复用的训练、评测和验证资产。

**一个基于 DeepSeek Harness 的 local-first trajectory compiler。**

[开始使用](#开始使用) · [你能得到什么](#你能得到什么) · [核心流程](#核心流程) · [架构](#基于-deepseek-harness) · [English](README.md)

[![CI](https://github.com/jinzijian/EvoTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/jinzijian/EvoTrace/actions/workflows/ci.yml)
[![DeepSeek Harness](https://img.shields.io/badge/built%20on-DeepSeek%20Harness-6e40c9.svg)](https://github.com/deepseek-ai/deepseek-harness)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

</div>

EvoTrace 会导入你电脑上已经存在的 coding-agent 工作历史，找出真正值得保留的 session，再将它们编译成
有证据的 preference data、可重放的 coding task、RL environment 和 execution reward candidate。原始数据与
最终资产都由你自己控制。

EvoTrace **不是另一个 coding agent**。你不需要改变现在使用 Claude Code 或 Codex 的方式。

> [!WARNING]
> EvoTrace 仍处于 early alpha，DeepSeek Harness 也是 developer preview。自动生成的任务和 verifier 都只是
> candidate；只有通过文档中的证据门槛和 Docker validation，才能进入更高状态。

## 开始使用

### 1. 安装

macOS、Linux 或 WSL：

```bash
curl -LsSf https://raw.githubusercontent.com/jinzijian/EvoTrace/main/install.sh | sh
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/jinzijian/EvoTrace/main/install.ps1 | iex
```

### 2. 启动

```bash
evotrace
```

EvoTrace 会打开 DeepSeek Harness Web app。在 **Settings** 中选择你的 Harness 支持的 provider，例如 DeepSeek、
OpenAI 或 Anthropic。本地 import 和确定性 mining 不需要模型；agent review、hardening、calibration 和
evolution 需要已配置的模型。

新 session 默认使用 Harness **Full access**。如果想使用更窄的 workspace sandbox：

```bash
DSH_PERMISSION_MODE=workspace-write evotrace
```

只有构建或验证可执行环境时才需要 Docker。

### 3. 构建第一个资产

在 app 中输入 `/`，然后依次运行：

```text
/init              导入已有 Claude Code 和 Codex 历史
/candidates        查看证据最强的 session
/show 1            检查 candidate 1 及缺失的证据
/review 1          运行四个 agent 的顺序 review
/assets            查看已编译或已验证的资产
```

这就是产品的主闭环。Review 可能构建资产，也可能把 session 路由到 preference data、仅作为
hardening seed 保留，或者给出明确理由后 reject。Reject 也是有价值的结果：它防止“很长但很弱”的
trajectory 被误标成 training-ready。

<p align="center">
  <img src="assets/evotrace-demo.gif" alt="使用 EvoTrace 导入、挖掘、review 与构建" width="900">
</p>

## 你能得到什么

| 产出 | 从 session 中恢复或生成的内容 | 用途 |
|---|---|---|
| **Candidate catalog** | task intent、repo、correction、failure、有效操作和 provenance gap | 从大量历史中找到真正值得保留的少数 session |
| **Preference / recovery data** | rejected/chosen attempt、人工纠正、成功恢复轨迹 | DPO、SFT、QA、failure-recovery training |
| **Executable task bundle** | repository base、initial state、dependency evidence、task specification | coding-agent eval、regression task、RL environment |
| **Verifier / reward candidate** | test command、behavioral check、policy 和 provenance | 通过验证后用于 rollout scoring 和 execution reward |
| **Difficulty evidence** | 全新独立 solver attempt 与 verifier outcome | 构建 curriculum，而不是靠 patch size 猜难度 |
| **Execution experience** | 从探索轨迹中压缩出的 grounded runtime fact | 训练样本与 held-out experience-transfer experiment |

同一个已验证任务，今天可以评测 agent，明天可以给新 rollout 打分，之后还可以产生 verifier-grounded
RL data。未来的可选 EvoTrace Marketplace 和微调服务接入，目标是让用户按自己的条件授权经过审核的资产；
它们目前是 roadmap，不是当前本地版本的已上线功能。

## 为什么 raw trajectory 还不是训练数据

一条 transcript 可能已经包含 prompt、message、command 和 diff，但 post-training 还需要：

- 一个连贯的任务边界，而不是整段 chat；
- 任务开始前的 repository state；
- 可重现的 dependency 和 execution environment；
- 能拒绝 base state、接受 known-good state 的独立 verifier；
- 把 task、patch、verifier 和 run 绑在一起的 provenance；
- 由全新 solver attempt 实测的难度，而不是 token 数或 patch 大小。

EvoTrace 通过 session import、Git/repository archaeology、确定性 gate、专用 agent 和隔离执行，自动补上这个
compilation gap。

## 核心流程

```text
Claude Code / Codex history
           │
           ▼
        /init         import + normalize + Git archaeology
           │
           ▼
     /candidates      证据排序，隐藏 nested subagent 重复项
           │
           ▼
       /review        episode mining → route gate → build/harden → critic
           │
      ┌────┴───────────┐
      ▼                    ▼
preference/recovery   executable candidate
                           │
                           ▼
                    /validate in Docker
                           │
                           ▼
                 verified reward environment
```

<p align="center">
  <img src="assets/evotrace-pipeline.svg" alt="EvoTrace trajectory-to-post-training pipeline" width="980">
</p>

### 状态就是证据边界

| 状态 | 真正含义 |
|---|---|
| **Mined** | session 有值得保留的信号，不代表可执行。 |
| **Buildable** | task、repo base、重建置信度、reference patch、verifier command 和 environment gate 全部通过。 |
| **Bundle generated** | Docker-ready candidate 已存在，但 verifier 还不可信。 |
| **Verified** | 合规 Docker run 已拒绝 base、接受 reference，且结果与确切 bundle digest 绑定。 |
| **Calibrated** | 全新 solver attempt 已实测难度；默认目标是 5 次中通过 2 次。 |

EvoTrace 对空 prompt wrapper、low-confidence reconstruction、缺失 reference patch、缺失 verification command、
不支持的环境、candidate 切换和不匹配的 asset lineage 全部 fail closed。

## 常用操作

### 不调用模型，只挖掘本地历史

```text
/init
/candidates
/show 1
```

### 编译并独立验证一个可执行任务

```text
/review 1
/build 1
/validate 1
/runs
```

### 让一个过于简单的 verified task 真正变难

```text
/harden 1
/calibrate 2
```

Hardening 必须增加可独立测试的 behavior、compatibility、edge case 或 failure constraint。单纯让 patch 更长
不算变难。

### 测试 execution experience 能否迁移

```text
/evolve 1 2
```

Asset 1 用于探索和压缩；asset 2 必须是同一 repo 中独立构建的 held-out task。然后用 Docker reward
比较 baseline 和 conditioned solver。不提供 held-out asset 的 `/evolve 1` 只是 wiring smoke test，不能证明 transfer。

### Command 速查

| Command | 用途 |
|---|---|
| `/init [all\|codex\|claude]` | 导入历史并刷新 mining |
| `/candidates` | 浏览排序后的 candidate |
| `/search payment retry` | 搜索 task、repository 和 evidence |
| `/show 1` | 检查 provenance 与 readiness gap |
| `/review 1` | 运行顺序 review pipeline |
| `/build 1` | 编译 execution candidate |
| `/validate 1` | 运行双状态 Docker validation |
| `/harden 1` | 派生并测试更难的 child task |
| `/calibrate 1` | 用 self-play 测量并调节难度 |
| `/evolve 1 2` | 在 held-out task 上测试压缩 experience |
| `/assets` | 列出编译资产与状态 |
| `/runs` | 检查已保存的 validation evidence |
| `/doctor` | 检查本地 integration |

## 基于 DeepSeek Harness

EvoTrace 是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的一个专用发行版。Harness 提供 Web UI、
session、streaming、provider settings、permission surface、slash command 和 plugin runtime。EvoTrace 在此基础上增加
trajectory compiler，以及一个 managed Orchestrator 和四个 foreground、least-privilege role：

| 阶段 | 责任 | 不能做 |
|---|---|---|
| **Episode Miner** | 隔离一个连贯 episode，统计有效操作 | 构建或批准资产 |
| **Candidate Gate** | 判断价值、复杂度、可重建性，并写入一次不可变 route | 修改数据或事后更改 route |
| **Task Builder / Hardener** | 构建确切 candidate，或派生更难 child | 修改源 checkout 或自我批准 |
| **Verifier Critic** | 审核 Docker run、verifier evidence、lineage 和难度 | 在缺失证据时做认证 |

四个 child 严格顺序执行，不会并行。Review-bound tool 在代码层绑定确切 review token、candidate ID、route
和 produced-asset lineage，而不是只靠 prompt 约束。

<p align="center">
  <img src="assets/evotrace-harness.png" alt="EvoTrace on DeepSeek Harness" width="900">
</p>

## 从源码安装

需要 Git、Node.js `22.19+` 或 `24+`、Python `3.9+`；Docker 为可选执行依赖。

```bash
git clone https://github.com/jinzijian/EvoTrace.git
cd EvoTrace
python3 -m venv .venv
.venv/bin/python -m pip install -e .
pnpm install
pnpm dev
```

Python CLI 仍作为确定性 compiler 与 automation sidecar 保留。机器化命令可用 `et --help` 查看；主界面是
DeepSeek Harness app。

## 执行与信任边界

- Import 和 mining 只读取本地 Claude Code/Codex history 与 Git evidence，不修改源 repository；
- Codex subagent 和 fork trajectory 保留 parent lineage，但在默认 candidate 列表中隐藏；
- Orchestrator 只暴露固定领域工具，不提供任意宿主 shell 或 filesystem tool；
- Validation 在一次性 Docker world 中运行，禁止源码 bind mount、Docker socket、host network、privileged、
  宿主 credential；
- Builder 与 Verifier Critic 是独立 child session，Builder 不能批准自己的 verifier；
- Self-play 和 evolution 是显式操作，因为它们会把选中的 task context 发给已配置的模型。

规范文档：[sandbox contract](docs/sandbox-contract.md)、[task quality standard](docs/task-quality-standard.md)、
[schema](docs/schema.md) 和 [design](docs/design.md)。

## 当前版本与 roadmap

当前开源版本已包含：本地 Claude Code/Codex import、证据 mining、顺序 agent review、environment reconstruction、
Docker bundle generation、双状态 validation、self-play calibration、语义化 task hardening、experience compression 和
held-out transfer measurement。

仍在开发：

- 更广泛的跨语言 dependency repair 和自动 environment construction；
- 更强的 hidden behavioral verifier 与 adversarial task mutation；
- 经过验证的 DPO、SFT 和 RL dataset exporter；
- 可选 Marketplace 与 managed fine-tuning integration。

## 致谢

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 是 EvoTrace 的应用与 agent foundation。
- [Microsoft RepoLaunch](https://github.com/microsoft/RepoLaunch) 是可重现 repository-to-environment construction 的主要启发。
  EvoTrace 的起点更早：先从真实 coding-agent 工作中挖掘任务和学习信号。

## License

Apache-2.0。见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
