# Trajectory and bundle schema

The schema is intentionally JSON-first and dependency-free. Version `0.1` is experimental.

## Normalized trajectory

Each session directory contains `trajectory.json` plus `events.jsonl`.

```json
{
  "schema_version": "0.1",
  "session_id": "codex-019f...",
  "created_at": "2026-08-19T12:00:00Z",
  "source": {
    "kind": "history_import",
    "agent": "codex"
  },
  "task": {
    "text": "Add cursor pagination without breaking existing clients.",
    "source": "history"
  },
  "repository": {
    "root": "/local/path",
    "base_commit": "abc123...",
    "base_commit_source": "session",
    "reconstruction_confidence": "high",
    "dirty": false
  },
  "verification": {
    "commands": []
  },
  "outcome": {
    "status": "imported"
  }
}
```

## Event envelope

Every JSONL record uses the same envelope:

```json
{
  "timestamp": "2026-08-19T12:00:01Z",
  "kind": "tool.call",
  "data": {
    "name": "exec_command",
    "arguments": {"cmd": "python -m pytest -q"},
    "call_id": "call_123"
  }
}
```

Known event kinds are:

- `message.user`
- `message.assistant`
- `tool.call`
- `tool.result`
- `process.started`
- `process.output`
- `process.completed`

Adapters may omit events that cannot be mapped without copying provider-specific internal state.

## Mined candidate

Each Harness `/mine` operation writes one JSON record per session under `candidates/`. A record contains a bounded score,
classification labels, human-readable evidence, and the raw signal counts used by the deterministic curator:

```json
{
  "schema_version": "0.1",
  "session_id": "codex-019f...",
  "curator": {
    "kind": "evidence_heuristic",
    "version": "0.3",
    "model_used": false
  },
  "score": 8,
  "labels": ["useful", "execution_verifiable", "recovery_trajectory"],
  "evidence": ["2 code-edit tool calls", "1 verification command(s) recovered"],
  "signals": {
    "human_corrected": false,
    "recovery_observed": true,
    "reconstruction_confidence": "high"
  }
}
```

An opt-in model-curation adapter may add a separate `model_review` object without replacing deterministic labels:

```json
{
  "model_review": {
    "provider": "openai",
    "model": "gpt-5.6-sol",
    "capsule_sha256": "...",
    "capsule_bytes": 4821,
    "data_policy": {
      "raw_transcript_included": false,
      "tool_output_included": false,
      "repository_source_included": false,
      "absolute_paths_included": false
    },
    "review": {
      "disposition": "keep",
      "asset_types": ["eval", "execution_reward"],
      "execution_readiness": "ready",
      "preference_readiness": "not_applicable"
    }
  }
}
```

The full structured review also records value, coding relevance, privacy risk, evidence, missing requirements,
response id, token usage, and timestamps. It is advisory; builders continue to trust only deterministic readiness
signals.

## Compiled task manifest

`task.json` is the canonical bundle manifest. It records the recovered task, source session, base commit,
environment inference, verifier configuration, and reproducibility evidence. `task.yaml` is a compact interchange
view for harnesses; consumers that need all fields should use `task.json`.

Every compiled bundle also includes `sandbox-policy.json`. V0.8 records `container_only` execution, an ephemeral
container copy, no host mounts or Docker socket, no privileged execution, no network by default, and output limited
to a new run directory. Runtime implementations must enforce rather than merely parse this policy.

## Saved run

A conforming validation provider saves one immutable `runs/<run-id>/run.json` record. It contains the session id,
run kind, timestamp, pass/score result, complete structured report, and a SHA-256 digest of the exact compiled
bundle. EvoTrace only displays an asset as `Verified` when a conforming `docker-validation` run still matches the
current bundle digest. The run must use the `docker-two-state-v0.1` protocol, reject the base with at least one
behavioral verifier failure, accept the reconstructed reference, and record the required sandbox controls. Editing
the bundle invalidates that presentation state. The Harness Validator exposes this operation through the fixed
`/validate` command; it never executes verifier code directly on the host.

## Self-play calibration

An opt-in calibration is stored under `calibrations/<session-id>/<calibration-id>/`. Its `calibration.json` binds
the target pass count, exact bundle digest, difficulty versions, pass rate, reference-equivalent patch count, and
every adjustment decision. Each version stores a rendered task plus hint list and verifier overlay. Each attempt
stores the candidate patch, bounded agent output, provider/model/session provenance, external-access audit, and the
Docker evaluation report.

`calibrated` means the requested count was observed for one unchanged difficulty version. `too_easy` and `too_hard`
are valid completed calibration outcomes, not infrastructure errors. A `self-play-calibration` run does not promote
an asset to `Verified`; only the independent two-state validation protocol can do that.

## Execution-experience evolution

An opt-in evolution is stored under `evolutions/<held-out-session-id>/<evolution-id>/`. Stage 1 stores an
instrumentation patch, a sanitized trajectory capsule, an execution-grounding grade, and model/access provenance.
Stage 2 stores a structured `execution-experience` packet plus compression statistics. Stage 3 stores paired fresh
baseline and experience-conditioned solver attempts evaluated by the same held-out Docker verifier.

`experience_verified` requires an independent same-repository held-out asset, at least three paired trials, a
passing exploration grade, and higher conditioned than baseline success. Reusing the source asset always yields
`smoke_only`, even if the conditioned solver improves. Evolution records also preserve the curriculum decision and
state that raw trajectories and reference patches were withheld from downstream solvers. The Explorer, Compressor,
and every paired solver attempt must also pass the recorded workspace-boundary audit.

## Portable export

The compiler's explicit local export operation creates an `.evotrace.tar.gz` containing the mined candidate, a
trajectory with local source and repository paths removed, normalized events, the compiled bundle, related
saved run records, local self-play calibrations, and execution-experience evolution artifacts. Export is never an upload. A candidate carrying `sensitive_content` requires a separate explicit
override. Known local run-directory metadata is removed, but export is not a publication approval: users must
review project-specific content before sharing or listing an asset.

## Compatibility policy

Until schema version `1.0`, minor releases may add or rename fields. Readers should ignore unknown fields and fail
clearly when they encounter an unsupported `schema_version`.
