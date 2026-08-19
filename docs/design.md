# Design principles

EvoTrace exists to answer a narrow question:

> How can real coding-agent work become reusable, verifiable evaluation and learning assets?

The compiler uses five principles.

Before compilation, V0.3 adds two local stages: an incremental importer and an evidence-based curator. The importer
normalizes Claude Code and Codex histories without copying raw logs. The curator separates preference candidates
from execution-verifiable candidates and records the concrete signals behind every label. It is deliberately a
deterministic heuristic in V0.3, not a hidden model judge.

## 1. Preserve the starting state

A task is not only a prompt. It is a prompt plus the state in which the request was made. EvoTrace records
the base commit, tracked dirty patch, and selected untracked files independently from the observed final state.
Replay restores only that initial state.

For history imports, the original dirty state may be unavailable. These bundles receive `medium` rather than
`high` reproducibility confidence. The label is an evidence boundary, not a quality score.

## 2. Keep the reference solution out of evaluation

The observed final patch is useful for analysis, task deduplication, and future verifier synthesis. It is not a
valid universal answer key: a different implementation may be equally correct. Generated verifiers therefore do
not apply or compare against `reference.patch`.

## 3. Make verifier provenance visible

Verifier commands have a strict provenance order:

1. `explicit`: provided by the user while recording or compiling;
2. `trajectory`: recovered from an allowlist of test, build, typecheck, and lint commands in tool calls;
3. `repository`: inferred from conservative repository conventions.

The bundle records this source in `verifier.json`. If no behavioral command is available, the compiler emits a
warning and uses only a weak repository-change check.

## 4. Avoid obvious false passes

The generated verifier samples candidate changes before it runs tests. This prevents test-created artifacts such
as `__pycache__` from satisfying the change requirement. Python bytecode is isolated to prevent stale cache reuse.
Generated caches and common build directories are excluded from the meaningful-change check.

When a recorded reference patch is available, existing test files that the reference did not change are marked as
protected. Editing one makes the verifier fail even if the edited test command exits successfully. The policy is
visible and editable in `verifier.json`; imported histories without a reference patch do not enable it.

This is a floor, not a complete reward-hacking defense. Future policies should add test-integrity checks, hidden
fixtures, side-effect assertions, performance constraints, and task-specific invariants.

## 5. Treat evaluation as a reusable learning substrate

Evaluation and training are not separate sinks. An executable task plus a validated verifier can benchmark an
agent, provide an RL environment, score newly sampled rollouts, and produce verifier-grounded trajectories for
subsequent SFT or RL. Raw transcripts are only source material; high-quality training data requires replay,
validation, provenance, and curation.

The current open-source release builds and inspects these local asset layers. Future distribution and training
surfaces—including a data marketplace and fine-tuning-service integrations—must remain opt-in. Only assets a user
explicitly reviews and selects may cross the local trust boundary.

## Trust boundaries

- A local history is sensitive input.
- A compiled bundle contains code and must be treated as sensitive until reviewed.
- Restoring a bundle extracts files but does not install dependencies.
- Running a verifier executes shell commands from the bundle.
- Host-side execution of a supplied agent command is prohibited; `benchmark --agent` is rejected.
- A future autonomous curator/builder/candidate agent must run only inside the container boundary defined by
  [`sandbox-contract.md`](sandbox-contract.md).

Do not restore, verify, or benchmark an untrusted bundle outside an isolated environment.
