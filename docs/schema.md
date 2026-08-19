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

## Compiled task manifest

`task.json` is the canonical bundle manifest. It records the recovered task, source session, base commit,
environment inference, verifier configuration, and reproducibility evidence. `task.yaml` is a compact interchange
view for harnesses; consumers that need all fields should use `task.json`.

## Compatibility policy

Until schema version `1.0`, minor releases may add or rename fields. Readers should ignore unknown fields and fail
clearly when they encounter an unsupported `schema_version`.
