<div align="center">

# EvoTrace

### Turn real-world Claude Code and Codex trajectories into trainable, verifiable, and tradable post-training assets.

[Quickstart](#quickstart) · [Why](#a-trajectory-is-not-yet-training-data) · [Agents](#three-agents-three-trust-boundaries) · [Security](#security) · [中文](README.zh-CN.md)

[![CI](https://github.com/jinzijian/EvoTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/jinzijian/EvoTrace/actions/workflows/ci.yml)
[![DeepSeek Harness](https://img.shields.io/badge/foundation-DeepSeek%20Harness-6e40c9.svg)](https://github.com/deepseek-ai/deepseek-harness)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## **Trainable · Verifiable · Tradable**

**The local-first compiler for real-world coding-agent experience.**

</div>

<p align="center">
  <img src="assets/evotrace-demo.gif" alt="EvoTrace turns real-world coding-agent trajectories into post-training assets" width="900">
</p>

## A trajectory is not yet training data

Claude Code and Codex already produce valuable real-world trajectories: failed attempts, human corrections,
discarded changes, recovery paths, tests, and successful implementations. But a raw transcript still lacks a
stable task boundary, reconstructable repository state, replayable environment, validated verifier, privacy
review, and provenance.

EvoTrace compiles that missing layer:

```text
Real-world Claude Code / Codex sessions
                    ↓
        import + Git/repo archaeology
                    ↓
        evidence-backed trajectory mining
                    ↓
 preference data  ·  RL environments  ·  execution rewards
                    ↓
       train locally · evaluate · license eligible assets
```

The same executable task can evaluate an agent today, generate and score new rollouts tomorrow, and become
high-quality verifier-grounded RL data. The future opt-in EvoTrace Marketplace will let users license reviewed,
rights-cleared assets on terms they control instead of surrendering raw history to a data intermediary.

EvoTrace also closes a second loop: an Explorer generates and executes repository questions, an Experience
Compressor distills the grounded trajectory, and fresh solver attempts test whether that packet improves a truly
held-out task. Compression is judged by downstream execution success—not by whether an LLM likes the summary.

```text
real repo → execution exploration → trajectory capsule → experience packet
                                                      ↓
                     held-out task: baseline vs conditioned solver
                                                      ↓
                         Docker reward → adaptive curriculum
```

> [!IMPORTANT]
> The Marketplace and managed fine-tuning integrations are roadmap products. The current open-source release
> works locally. Nothing is uploaded, sold, or shared by default.

## Built on DeepSeek Harness

EvoTrace is now a specialized distribution of
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), not a separate ad-hoc chat UI.

DeepSeek Harness supplies the browser/agent shell, sessions, streaming, slash-command surface, provider settings,
credential storage, approvals, and plugin runtime. EvoTrace supplies the domain composition:

- Claude Code and Codex history import;
- trajectory mining and evidence catalog;
- one Orchestrator that opens four least-privilege DeepSeek subagents in sequence;
- fixed, allowlisted compiler tools backed by the existing Python core;
- EvoTrace branding and onboarding copy;
- a Docker-only contract for future autonomous execution.

The Python package is now an internal deterministic compiler sidecar. It no longer owns the product interface,
model routing, or agent loop.

<p align="center">
  <img src="assets/evotrace-harness.png" alt="EvoTrace DeepSeek Harness home" width="900">
</p>

## Quickstart

### One-line install — macOS, Linux, WSL

```bash
curl -LsSf https://raw.githubusercontent.com/jinzijian/EvoTrace/main/install.sh | sh
```

Then launch the app:

```bash
evotrace
```

New sessions default to the Harness **Full access** permission preset. To start
with the narrower workspace sandbox instead, run
`DSH_PERMISSION_MODE=workspace-write evotrace`.

### One-line install — Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/jinzijian/EvoTrace/main/install.ps1 | iex
evotrace
```

### From source

Requires Git, Node.js `22.19+` or `24+`, Python `3.9+`, and Docker for isolated execution work.

```bash
git clone https://github.com/jinzijian/EvoTrace.git
cd EvoTrace
python3 -m venv .venv
.venv/bin/python -m pip install -e .
pnpm install
pnpm dev
```

On first launch:

1. choose a workspace;
2. optionally configure DeepSeek, OpenAI, or Anthropic in Settings;
3. type `/` to open the command palette;
4. run `/init` to index existing Claude Code and Codex history.

Useful commands inside the app:

```text
/init [all|codex|claude]    import existing history and refresh mining
/candidates                browse ranked, evidence-backed candidates
/search payment retry      search tasks, repos, and evidence
/show 1                    inspect provenance and readiness gaps
/review 1                  run the four-agent sequential review pipeline
/build 1                   compile a Docker-ready asset candidate
/validate 1                reject the base and accept the reference in Docker
/calibrate 1               run 5 DeepSeek attempts and target 2 verifier passes
/harden 1                  derive, verify, and self-play a harder child task
/evolve 1 2                explore asset 1, test compressed experience on held-out asset 2
/assets                    inspect compiled assets
/runs                      inspect saved validation evidence
/doctor                    check local integrations
```

These are native Harness slash commands, and the same operations are exposed as typed agent tools. API keys are
entered in the Harness Settings UI; EvoTrace does not accept keys in slash commands or store them in trajectories.

## One orchestrator, four sequential subagents

`/review <candidate>` starts one model turn. The EvoTrace Orchestrator then waits for each foreground DeepSeek
Harness child before starting the next one, so downstream stages receive upstream evidence instead of producing
four unrelated votes. Candidate Gate must first record one immutable route: `direct`, `derived_seed`,
`preference_only`, or `reject`. EvoTrace binds that decision to the exact candidate ID; build/harden tools reject
candidate switching, and validation is bound to the asset actually produced by the hardener.

| Stage | Can do | Cannot do |
|---|---|---|
| **Episode Miner** | bound the coherent episode and count effective actions | use raw length as proof, build or approve an asset |
| **Candidate Gate** | judge value, complexity, and reconstructability | mutate data, collapse all axes into one score |
| **Task Builder / Hardener** | compile a direct bundle or derive a harder child through fixed tools | edit the source checkout, approve its own verifier |
| **Verifier Critic** | run fixed Docker validation and opt-in self-play; audit provenance and runs | run verifier code on the host, certify missing evidence |

Only the EvoTrace Orchestrator is exposed in the product roster. Each role is a fresh, foreground, one-shot child
session with a role-specific tool allowlist and no delegation tools. The generic DeepSeek Harness coding presets
are intentionally excluded because they have a different host-access contract.

## What an asset contains

| Layer | Evidence or output | Uses |
|---|---|---|
| Preference/correction | human edits, rejected/chosen attempts, successful recovery | DPO, SFT, QA, preference learning |
| Executable task | task intent, repository base, initial state, environment evidence | agent evals, regression tasks, RL environments |
| Verifier/reward candidate | test commands, checks, provenance, sandbox policy | rollout scoring and execution rewards after validation |
| Validated trajectory | replayed rollout plus independent verifier evidence | high-quality SFT and RL post-training data |
| Execution experience | runtime facts, commands, code locations, failures, compression provenance | conditioning, curriculum learning, and training examples after held-out validation |

`Mined`, `Buildable`, `bundle generated`, and `Verified` are different states. A history import is buildable only
when it has a meaningful task, an execution-verifiable route, a repository base, medium-or-better reconstruction,
a recovered reference patch, runnable verification commands, and a supported environment. Empty attachment
wrappers, low-confidence reconstructions, and missing Node manifests fail closed instead of producing weak
bundles. EvoTrace never promotes a generated verifier to `Verified` merely because an LLM wrote it.

Codex subagent/fork trajectories remain indexed with parent lineage for audit and future preference mining, but
they are hidden from the default candidate list so one parent session is not counted as several independent tasks.
Use `/candidates --all` to inspect them explicitly.

<p align="center">
  <img src="assets/evotrace-pipeline.svg" alt="EvoTrace trajectory-to-post-training pipeline" width="980">
</p>

## Security

- Importers read Claude Code/Codex history and Git evidence without modifying the source files.
- The Orchestrator and its leaf subagents expose fixed domain operations, not arbitrary host shell or filesystem tools.
- DSH telemetry is disabled by the EvoTrace launcher unless the user explicitly overrides it.
- Local product state lives under `~/.evotrace/`; Harness state is isolated under `~/.evotrace/harness/`.
- Verifier validation runs in a fresh Docker world with no host
  source bind mount, Docker socket, host network, privileged mode, host credentials, or arbitrary output path.
- Builder and Verifier Critic are separate child sessions. A builder cannot approve its own verifier.
- `/review` is strictly sequential: Episode Miner → Candidate Gate → Task Builder/Hardener → Verifier Critic.
- `/calibrate` is explicitly opt-in because task context is sent to the configured DeepSeek provider. Every attempt starts from a fresh task-only workspace; the reference stays hidden, and final scoring runs in Docker without host mounts.
- `/evolve` is also opt-in. Exploration runs in a disposable workspace sandbox; hidden reasoning is excluded from the saved capsule, secrets and absolute paths are redacted, the raw capsule is withheld from downstream solvers, and candidate patches are scored in Docker.
- Explorer, Compressor, and every paired solver attempt must pass the workspace-boundary access audit before an evolution can be certified.

Read the normative [sandbox contract](docs/sandbox-contract.md) and [design](docs/design.md).

> [!WARNING]
> EvoTrace is early alpha, and DeepSeek Harness is currently a developer preview. Mining labels and generated
> verifier candidates are evidence, not proof. Review every asset before training on it or sharing it.

## Current status

V0.8 now includes:

- a real DeepSeek Harness Web app with EvoTrace title, mark, onboarding, and provider settings;
- one managed EvoTrace Orchestrator with four foreground, least-privilege subagent roles;
- native `/init`, `/import`, `/mine`, `/candidates`, `/search`, `/show`, `/review`, `/build`, `/harden`, `/validate`, `/calibrate`, `/evolve`, `/assets`, `/runs`, `/doctor` flows;
- Claude Code and Codex import, edit-event reference reconstruction, deterministic mining, bundle generation, provenance, and privacy gates;
- fail-closed build-readiness gates, command-aware Python/Node environment inference, and default deduplication of nested Codex subagents;
- immutable `/review` route tokens and produced-asset lineage, preventing downstream stages from mutating a different candidate;
- Docker-only two-state verifier validation: the behavioral verifier must reject the base state and accept the reference state;
- immutable run evidence tied to the exact bundle digest; only a conforming Docker run promotes an asset to `Verified`.
- DeepSeek self-play difficulty calibration: independent five-way attempts, target-pass tracking, automatic hint addition/removal, reference-gated verifier overlays, and a `too_easy`/`too_hard` result when a task cannot be honestly forced into the target bucket.
- execution-experience evolution: autonomous runtime exploration, grounded trajectory compression, paired baseline/conditioned held-out solving, Docker rewards, measured utility, and curriculum feedback.

`/evolve <source> <held-out>` requires two independently built tasks from the same repository for a certifying
functional-compression result. Omitting the second asset deliberately produces `smoke_only`: it verifies the
wiring but cannot prove transfer because the source and evaluation task are identical.

Still in progress:

- broader cross-language dependency repair and autonomous environment-builder loops;
- semantic held-out task generation and adversarial task mutation for assets that remain too easy after all legitimate hints are removed;
- validated DPO/SFT/RL dataset exporters;
- the opt-in Marketplace and fine-tuning-service integrations.

## Acknowledgements

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) is the application and agent foundation.
- [Microsoft RepoLaunch](https://github.com/microsoft/RepoLaunch) is a primary inspiration for reproducible
  repository-to-environment construction. EvoTrace begins earlier, by mining tasks and learning signals from lived
  coding-agent work.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
