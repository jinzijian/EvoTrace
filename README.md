<div align="center">

# EvoTrace

### Turn every Claude Code and Codex session into reusable training, evaluation, and verification assets.

[Quickstart](#-quickstart) · [What you get](#what-you-get) · [How it works](#how-it-works) · [Security](#security-model) · [中文](README.zh-CN.md)

[![CI](https://github.com/jinzijian/evotrace/actions/workflows/ci.yml/badge.svg)](https://github.com/jinzijian/evotrace/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Your agent sessions are not disposable chat logs. They are compounding data assets.**

</div>

<p align="center">
  <img src="assets/evotrace-terminal.svg" alt="EvoTrace turns local agent history into reusable assets" width="900">
</p>

<p align="center"><sub>Illustrative output. EvoTrace reports the counts found in your own local history.</sub></p>

## ⚡ Quickstart

### macOS, Linux, or WSL

```bash
curl -LsSf https://raw.githubusercontent.com/jinzijian/evotrace/main/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/jinzijian/evotrace/main/install.ps1 | iex
```

Then run one command:

```bash
evotrace init
```

EvoTrace discovers existing Claude Code and Codex sessions, indexes them locally, and mines useful candidates.
The installer only installs the CLI—it does not read session data. You can [inspect the Unix installer](install.sh)
or [the PowerShell installer](install.ps1) before running it.

Already use [`uv`](https://docs.astral.sh/uv/)?

```bash
uv tool install git+https://github.com/jinzijian/evotrace.git
evotrace init
```

Prerequisites: Git plus `uv`, `pipx`, or Python 3.9+. Docker is only needed when running sandboxed execution.
`et` is the short command; the older CLI names remain compatibility aliases.

## What you get

| Asset | Recovered from your sessions | Useful for |
|---|---|---|
| Training | human corrections, preference pairs, successful recoveries | DPO, SFT, QA, data curation |
| Evaluation | task intent, repository base, environment evidence | replayable coding-agent evals |
| Verification | test commands, execution results, behavioral checks | Docker verifiers and execution rewards |

EvoTrace works with the agents developers already use. No proxy, hosted agent, or new editor is required. The V0.3
curator is deterministic and auditable: it does not call an LLM or upload session data.

> [!WARNING]
> EvoTrace is an early alpha. Mining labels and generated verifiers are evidence, not proof of task quality
> or semantic correctness. Inspect every eval before relying on it or sharing it.

## How it works

<p align="center">
  <img src="assets/evotrace-pipeline.svg" alt="Claude Code and Codex histories become training, evaluation, and verification assets through a local pipeline" width="980">
</p>

<p align="center"><sub>The host-side pipeline is local-first. Autonomous work is restricted to an ephemeral Docker workspace.</sub></p>

## Core workflow

### `evotrace init` — import and mine in one command

```bash
evotrace init
evotrace init --source codex --last 50
```

Use `init` on day one. It combines automatic discovery, incremental import, and local mining into a single
onboarding command. The commands below expose each stage when you want more control.

### `evotrace import` — index history you already have

```bash
# Discover both sources and index all available sessions.
evotrace import

# Limit or select a source.
evotrace import codex
evotrace import claude --last 20

# Import exact files.
evotrace import codex ~/.codex/sessions/2026/08/19/rollout-*.jsonl
evotrace import codex ~/.codex/history.jsonl
evotrace import claude ~/.claude/projects/my-project/session.jsonl
```

The importer honors `$CODEX_HOME` and `$CLAUDE_CONFIG_DIR`. For Codex it recognizes rich session JSONL files and
the lighter prompt history separately, keeping the richer copy when both identify the same session. Claude Code
documents plaintext session transcripts under `~/.claude/projects/` and a default 30-day cleanup window;
installing EvoTrace early preserves a normalized local index before old transcripts disappear. See the
[Claude Code session docs](https://code.claude.com/docs/en/sessions),
[Claude Code data-path docs](https://code.claude.com/docs/en/claude-directory),
[Codex configuration reference](https://developers.openai.com/codex/config-reference), and
[Codex CLI resume reference](https://developers.openai.com/codex/cli/reference).

Imports are incremental: unchanged source files are skipped using size and modification-time fingerprints. Use
`--refresh` to force re-indexing. Raw history files are read in place, never copied into the EvoTrace store.

### `evotrace mine` — find valuable experience

```bash
evotrace mine
evotrace mine --source codex --min-score 4
evotrace mine --json
```

V0.3 scores only observable signals: non-trivial task text, code-edit calls, verification commands, failed then
successful checks, human corrections after agent work, and repository reconstruction confidence. Every candidate
contains its score, labels, signals, and human-readable evidence in `~/.evotrace/candidates/`.

- `preference_candidate`: a likely rejected/chosen or correction pair for DPO, preference, QA, or SFT curation.
- `execution_verifiable`: code changes plus recovered verification and a reconstructable repository base.
- `recovery_trajectory`: a failure or correction followed by subsequent repair work.

This deliberately avoids model-judged labels in the first release. A model curator can later sit behind the same
schema without weakening provenance.

### `evotrace build` — compile executable eval assets

```bash
# Build the highest-ranked execution-verifiable candidates.
evotrace build --limit 10

# Build one session and optionally add trusted verifier commands.
evotrace build SESSION_ID \
  --verify "python -m pytest tests/integration -q" \
  --verify "python -m ruff check src"
```

A history transcript is not a complete environment. The builder combines session evidence with the local Git
repository: it uses a commit captured by the session when available, otherwise tries a time-aligned Git commit,
and records reconstruction confidence instead of pretending the result is exact. It then emits:

<p align="center">
  <img src="assets/evotrace-bundle.svg" alt="Example EvoTrace build output and generated eval bundle" width="920">
</p>

<p align="center"><sub>Example build output. Every recovered task and verifier retains inspectable provenance.</sub></p>

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

Verifier provenance is always visible: explicit user command, trajectory-recovered command, repository convention,
or a warning that no behavioral verifier was found.

## Day two and beyond

Keep the local index current without placing EvoTrace in front of either agent:

```bash
evotrace watch                 # poll every five minutes and re-run mining
evotrace watch --interval 60
evotrace watch --once          # useful in cron or a nightly job
```

The watcher reads changed history files only. It does not modify Claude Code, Codex, or source repositories.

## Security model

### Container-only agent boundary

The host-side importer and builder may read session files and Git objects, but they never give an autonomous agent
a writable host checkout. Each bundle contains an explicit `sandbox-policy.json` and a non-root Dockerfile. The
contract is:

- source enters as an archive copied into the image, not as a writable bind mount;
- no Docker socket, privileged mode, host PID namespace, or host credentials;
- runtime network is off by default, Linux capabilities are dropped, and new privileges are blocked;
- the agent may freely edit or delete its ephemeral `/workspace` copy;
- host output is promoted only into a new, unique run directory after validation;
- internal systems are exposed only through explicit read-only adapters or deterministic mocks—never production
  write credentials.

For this reason V0.3 rejects legacy `benchmark --agent` host execution. The safe container orchestrator is the next
runtime milestone; today `evotrace build` produces its complete, inspectable input. Existing candidate checkouts can still
be scored explicitly with `evotrace benchmark ... --candidate NAME=PATH`.

Read the full [sandbox contract](docs/sandbox-contract.md) and [security model](SECURITY.md).

### Storage and privacy

The default store is `~/.evotrace/`; override it with `$EVOTRACE_HOME` or `--home`. If an existing
`~/.scaleverifier/` store is present and the new path does not yet exist, EvoTrace reuses it automatically.

- No account, hosted LLM, API key, telemetry endpoint, payment flow, or data upload is required.
- Normalized text receives best-effort secret redaction.
- Git-ignored files, common `.env` files, and private-key suffixes are excluded from untracked snapshots.
- A compiled bundle contains source code and may still contain tracked secrets. Treat it as private until reviewed.

## Current scope

Implemented in V0.3:

- one-command `evotrace init` onboarding and macOS/Linux/WSL/Windows installers;
- full and incremental Claude Code / Codex history discovery;
- rich-session versus prompt-history precedence;
- normalized, redacted local trajectories;
- auditable preference, execution, correction, recovery, and low-value mining;
- Git-time reconstruction confidence;
- task, environment, Dockerfile, and verifier bundle generation;
- explicit container-only policy with non-root images and no host-agent fallback;
- replay, verifier execution, and existing-candidate scoring from V0.1.

Next milestones:

- a sandboxed curator/builder agent that can generate mocks and improve verifier coverage;
- automatic verifier validation and reward-hacking checks;
- opt-in read-only internal-service adapters and record/replay mocks;
- export adapters for DPO, preference, QA, SFT, and executable-eval datasets;
- deduplication, difficulty estimation, and benchmark registries.

See [docs/design.md](docs/design.md) and [docs/schema.md](docs/schema.md) for the data model.

## Community and development

Ask questions, share aggregate results, and propose new history adapters in
[GitHub Discussions](https://github.com/jinzijian/evotrace/discussions). Report reproducible problems through
[GitHub Issues](https://github.com/jinzijian/evotrace/issues). Never post raw trajectories or unreviewed bundles.

```bash
git clone https://github.com/jinzijian/evotrace.git
cd evotrace
uv sync
uv run python -m unittest discover -s tests -v
uvx ruff check src tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for adapter requirements and the pull-request checklist.

## License

Apache License 2.0.
