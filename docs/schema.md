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

Each `vf mine` pass writes one JSON record per session under `candidates/`. A record contains a bounded score,
classification labels, human-readable evidence, and the raw signal counts used by the deterministic curator:

```json
{
  "schema_version": "0.1",
  "session_id": "codex-019f...",
  "curator": {
    "kind": "evidence_heuristic",
    "version": "0.2",
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

## Compiled task manifest

`task.json` is the canonical bundle manifest. It records the recovered task, source session, base commit,
environment inference, verifier configuration, and reproducibility evidence. `task.yaml` is a compact interchange
view for harnesses; consumers that need all fields should use `task.json`.

Every compiled bundle also includes `sandbox-policy.json`. V0.2 records `container_only` execution, an ephemeral
container copy, no host mounts or Docker socket, no privileged execution, no network by default, and output limited
to a new run directory. Runtime implementations must enforce rather than merely parse this policy.

## Compatibility policy

Until schema version `1.0`, minor releases may add or rename fields. Readers should ignore unknown fields and fail
clearly when they encounter an unsupported `schema_version`.
