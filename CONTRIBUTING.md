# Contributing to EvoTrace

EvoTrace is early. Small, evidence-backed pull requests are easier to review than broad framework rewrites.

## Development setup

```bash
git clone https://github.com/jinzijian/EvoTrace.git
cd EvoTrace
uv sync
pnpm install
uv run python -m unittest discover -s tests -v
uvx ruff check .
pnpm test:harness
pnpm harness:doctor
```

Node.js `22.19+` or `24+` is required by the Harness application; Python `3.9+` runs the deterministic compiler
sidecar. Keep model/provider behavior in the Harness composition and fixed domain operations in `harness/plugins/`.
Do not add a second chat UI or another agent loop.

## Good first contributions

- Add a sanitized fixture for a coding-agent history format.
- Improve task recovery without copying provider-specific hidden state.
- Add a conservative verifier-command recognizer.
- Add a repository environment detector.
- Add an adversarial test that demonstrates a false pass.
- Improve a Curator, Builder, or Validator tool without broadening its host permissions.
- Improve Windows compatibility without weakening Unix behavior.

## Harness contribution rules

- Only EvoTrace-managed role presets belong in the product roster.
- Role tools call allowlisted compiler operations without a shell; generic shell/filesystem tools are excluded.
- Builder and Validator permissions remain separate.
- Autonomous environment construction or verifier execution must implement `docs/sandbox-contract.md` in Docker.
- Provider credentials stay in Harness settings and must never enter a trajectory, fixture, log, or command argument.

## Adapter requirements

History adapters must:

- stream or parse local files without modifying them;
- emit normalized event envelopes;
- apply best-effort redaction before persistence;
- avoid copying hidden reasoning or encrypted provider state;
- include sanitized fixtures and tests;
- document what cannot be reconstructed.

## Pull request checklist

- Tests cover the change and relevant failure mode.
- `uv run python -m unittest discover -s tests -v` passes.
- `uvx ruff check .` passes.
- `pnpm test:harness` passes for Harness or integration changes.
- Documentation describes new user-visible behavior.
- No real trajectory, credential, proprietary code, or personal path was committed.
