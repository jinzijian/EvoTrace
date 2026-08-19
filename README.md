<div align="center">

# ScaleVerifier

### Import your Claude Code and Codex history. Turn the best sessions into reusable coding-agent evals.

[![CI](https://github.com/jinzijian/scaleverifier/actions/workflows/ci.yml/badge.svg)](https://github.com/jinzijian/scaleverifier/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[简体中文](README.zh-CN.md)

**Keep using the agents you already like. ScaleVerifier is the local asset compiler behind them.**

</div>

ScaleVerifier does not ask users to adopt another coding agent or route work through a proxy. It indexes local
Claude Code and Codex sessions, finds the trajectories worth preserving, and reconstructs tasks, environments,
and verifier candidates from real work.

```text
Claude Code / Codex history
            +
      local Git archaeology
            ↓
     normalized trajectory
            ↓
        local curator
       ┌────┴─────┐
       ↓          ↓
 preference    executable
 DPO / SFT     task + env
 QA / pairs    Docker + verifier
```

> [!WARNING]
> ScaleVerifier is an early alpha. Mining labels and generated verifiers are evidence, not proof of task quality
> or semantic correctness. Inspect every eval before relying on it or sharing it.

## The day-one experience

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

The numbers above illustrate the UX; ScaleVerifier reports the counts found in your own local history. The V0.2
curator is a deterministic, auditable heuristic. It does not call an LLM or upload session data.

## Install

With [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/jinzijian/scaleverifier.git
vf doctor
```

For development:

```bash
git clone https://github.com/jinzijian/scaleverifier.git
cd scaleverifier
uv sync
uv run vf doctor
```

ScaleVerifier has no runtime Python dependencies. Git is required; Docker is used for sandboxed execution.
`scaleverifier` and `sv` remain aliases for `vf`.

## Three commands

### `vf import` — index history you already have

```bash
# Discover both sources and index all available sessions.
vf import

# Limit or select a source.
vf import codex
vf import claude --last 20

# Import exact files.
vf import codex ~/.codex/sessions/2026/08/19/rollout-*.jsonl
vf import codex ~/.codex/history.jsonl
vf import claude ~/.claude/projects/my-project/session.jsonl
```

The importer honors `$CODEX_HOME` and `$CLAUDE_CONFIG_DIR`. For Codex it recognizes rich session JSONL files and
the lighter prompt history separately, keeping the richer copy when both identify the same session. Claude Code
documents plaintext session transcripts under `~/.claude/projects/` and a default 30-day cleanup window;
installing ScaleVerifier early preserves a normalized local index before old transcripts disappear. See the
[Claude Code session docs](https://code.claude.com/docs/en/sessions),
[Claude Code data-path docs](https://code.claude.com/docs/en/claude-directory),
[Codex configuration reference](https://developers.openai.com/codex/config-reference), and
[Codex CLI resume reference](https://developers.openai.com/codex/cli/reference).

Imports are incremental: unchanged source files are skipped using size and modification-time fingerprints. Use
`--refresh` to force re-indexing. Raw history files are read in place, never copied into the ScaleVerifier store.

### `vf mine` — find valuable experience

```bash
vf mine
vf mine --source codex --min-score 4
vf mine --json
```

V0.2 scores only observable signals: non-trivial task text, code-edit calls, verification commands, failed then
successful checks, human corrections after agent work, and repository reconstruction confidence. Every candidate
contains its score, labels, signals, and human-readable evidence in `~/.scaleverifier/candidates/`.

- `preference_candidate`: a likely rejected/chosen or correction pair for DPO, preference, QA, or SFT curation.
- `execution_verifiable`: code changes plus recovered verification and a reconstructable repository base.
- `recovery_trajectory`: a failure or correction followed by subsequent repair work.

This deliberately avoids model-judged labels in the first release. A model curator can later sit behind the same
schema without weakening provenance.

### `vf build` — compile executable eval assets

```bash
# Build the highest-ranked execution-verifiable candidates.
vf build --limit 10

# Build one session and optionally add trusted verifier commands.
vf build SESSION_ID \
  --verify "python -m pytest tests/integration -q" \
  --verify "python -m ruff check src"
```

A history transcript is not a complete environment. The builder combines session evidence with the local Git
repository: it uses a commit captured by the session when available, otherwise tries a time-aligned Git commit,
and records reconstruction confidence instead of pretending the result is exact. It then emits:

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

Keep the local index current without placing ScaleVerifier in front of either agent:

```bash
vf watch                 # poll every five minutes and re-run mining
vf watch --interval 60
vf watch --once          # useful in cron or a nightly job
```

The watcher reads changed history files only. It does not modify Claude Code, Codex, or source repositories.

## Container-only agent boundary

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

For this reason V0.2 rejects legacy `benchmark --agent` host execution. The safe container orchestrator is the next
runtime milestone; today `vf build` produces its complete, inspectable input. Existing candidate checkouts can still
be scored explicitly with `vf benchmark ... --candidate NAME=PATH`.

Read the full [sandbox contract](docs/sandbox-contract.md) and [security model](SECURITY.md).

## Storage and privacy

The default store is `~/.scaleverifier/`; override it with `$SCALEVERIFIER_HOME` or `--home`.

- No account, hosted LLM, API key, telemetry endpoint, payment flow, or data upload is required.
- Normalized text receives best-effort secret redaction.
- Git-ignored files, common `.env` files, and private-key suffixes are excluded from untracked snapshots.
- A compiled bundle contains source code and may still contain tracked secrets. Treat it as private until reviewed.

## Current scope

Implemented in V0.2:

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

## License

Apache License 2.0.
