# EvoTrace on DeepSeek Harness

EvoTrace exists to answer a narrow question:

> How can real coding-agent work become reusable, verifiable evaluation and learning assets?

EvoTrace is a specialized [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) distribution.
Harness owns the Web application, session lifecycle, model/provider configuration, streaming, command palette,
approval surface, and plugin runtime. EvoTrace owns the product composition, role presets, trajectory tools,
deterministic compiler, provenance model, and Docker-only execution contract.

The current architecture is intentionally split into two planes:

```text
DeepSeek Harness product plane
  Web UI · model settings · sessions · slash commands · role presets
                         ↓ fixed typed tools
EvoTrace compiler plane
  import · mine · catalog · reconstruct · build · provenance
                         ↓ Docker execution world
  dependency setup · base/reference validation · self-play scoring · run evidence
```

The compiler plane normalizes Claude Code and Codex histories without modifying raw logs. Its evidence-based miner
separates preference candidates from execution-verifiable candidates and records the concrete signals behind every
label. A model may help curate or explain those records, but model judgment remains separate provenance and cannot
override deterministic build or validation gates.

## Interaction model

Running `evotrace` launches the branded Harness Web app. The app publishes one managed **EvoTrace Orchestrator**.
`/review <candidate>` makes it start four fresh foreground children in a fixed order: Episode Miner, Candidate Gate,
Task Builder/Hardener, and Verifier Critic. Each child receives only its role-specific tools, and the next stage does
not start until the previous one returns.

Candidate Gate must record exactly one immutable route (`direct`, `derived_seed`, `preference_only`, or `reject`)
for the selected candidate. Mutation tools require the route token and exact candidate ID. Validation and
calibration additionally require the exact child asset emitted by build/harden, so model text cannot silently
substitute a different candidate between stages.

Typing `/` opens native Harness commands for the same allowlisted operations. The existing Python CLI remains an
internal compiler sidecar and machine integration surface; it is no longer the primary product interface or agent
loop. Generic Harness coding presets are not included in the EvoTrace roster because they expose a broader host
access model than this product permits.

## 1. Preserve the starting state

A task is not only a prompt. It is a prompt plus the state in which the request was made. EvoTrace records
the base commit, tracked dirty patch, and selected untracked files independently from the observed final state.
Replay restores only that initial state.

For history imports, the original dirty state may be unavailable. These bundles receive `medium` rather than
`high` reproducibility confidence. The label is an evidence boundary, not a quality score.

Imported candidates with `low` or `none` reconstruction confidence are not buildable. The compiler also requires a
meaningful task, a repository base, a reference patch, at least one behavioral verification command, and an
environment supported by the inferred command/runtime contract. These are deterministic gates, not model votes.

Codex subagent and fork sessions retain `parent_session_id` and `agent_path` lineage. They are excluded from the
default candidate surface to avoid treating one orchestrated review as several independent real-world tasks, but
remain available through the explicit all-candidates view.

## 2. Keep the reference solution out of evaluation

The observed final patch is useful for analysis, task deduplication, verifier validation, and future verifier
synthesis. It is not a valid universal answer key: a different implementation may be equally correct. Candidate
scoring never compares a rollout with `reference.patch`; only the independent validation stage applies the reference
inside a disposable container to prove that the recovered verifier accepts at least one known-good state.

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

## 6. Calibrate difficulty with honest self-play

`/calibrate` runs the configured DeepSeek Harness solver several times from independent task-only workspaces. The
default target is two verifier passes in five attempts. A difficulty version consists of the immutable original
task, a reversible list of hints, and a verifier overlay; the canonical bundle and reference patch are not changed.

When the task is too hard, EvoTrace may add bounded hints derived from existing test commands, changed-file
provenance, or saved base failure evidence. When it is too easy, it removes hints first. A proposed verifier command
is accepted only after the hidden reference passes it in Docker. Each solver patch is then applied to the task-only
image and scored with no host mounts or network.

Calibration is not allowed to manufacture a desired success rate. If every passing patch is equivalent to the
known reference, EvoTrace records `too_easy` instead of rejecting correct solutions. Such a task should move to an
easier curriculum bucket or be replaced by a semantically harder task. Likewise, exhausting safe hints records
`too_hard`. The full versions, attempts, patches, model provenance, Docker reports, and adjustment decisions remain
local under `calibrations/` and are included in an explicit portable export.

## Relationship to RepoLaunch

[Microsoft RepoLaunch](https://github.com/microsoft/RepoLaunch) is the primary technical inspiration for the
executable-environment portion of this design. RepoLaunch accepts repository metadata and uses an agentic
setup/verify/organize loop to produce a Docker image, rebuild and test commands, a test-log parser, and structured
test statuses. EvoTrace begins before that boundary by extracting candidate tasks and learning signals from lived
Claude Code and Codex sessions.

The planned adapter seam follows RepoLaunch's conceptual input/output contract:

```text
EvoTrace executable candidate
  {repo, base_commit, language, created_at, hints}
                    ↓
optional environment backend
                    ↓
  {image, setup, rebuild, tests, parser, statuses}
                    ↓
EvoTrace verifier validation + trajectory/reward assets
```

This separation avoids rebuilding a cross-language environment agent inside the curator. EvoTrace owns session
import, task selection, preference/recovery extraction, provenance, privacy, and asset distribution. An optional
RepoLaunch-compatible backend owns environment setup and build/test discovery. The current release does not vendor
or invoke RepoLaunch code.

## Trust boundaries

- A local history is sensitive input.
- A compiled bundle contains code and must be treated as sensitive until reviewed.
- Restoring a bundle on the host extracts files but does not install dependencies.
- `/validate` builds dependencies into an image, then executes verifier commands only in disposable containers.
- Host-side execution of a supplied agent command is prohibited; `benchmark --agent` is rejected.
- EvoTrace Harness roles receive different fixed tool catalogs. None receives arbitrary host shell or filesystem
  tools. The parent Orchestrator can read candidate evidence but cannot call generic mutation tools; route-bound
  wrappers expose build/harden only after Candidate Gate, and validation only for the produced asset lineage.
- Cloud model use requires explicit provider configuration. API keys stay in Harness provider settings and are not
  accepted in trajectory commands or copied into compiler records.
- A future autonomous environment-builder, verifier-writer, or candidate agent must run only inside the container boundary defined by
  [`sandbox-contract.md`](sandbox-contract.md).

Do not restore, verify, or benchmark an untrusted bundle outside an isolated environment.
