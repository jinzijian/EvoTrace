# Contributing to ScaleVerifier

ScaleVerifier is early. Small, evidence-backed pull requests are easier to review than broad framework rewrites.

## Development setup

```bash
git clone https://github.com/jinzijian/scaleverifier.git
cd scaleverifier
uv sync
uv run python -m unittest discover -s tests -v
uvx ruff check src tests
```

The runtime package intentionally has no third-party dependencies. Discuss a new runtime dependency before adding
it.

## Good first contributions

- Add a sanitized fixture for a coding-agent history format.
- Improve task recovery without copying provider-specific hidden state.
- Add a conservative verifier-command recognizer.
- Add a repository environment detector.
- Add an adversarial test that demonstrates a false pass.
- Improve Windows compatibility without weakening Unix behavior.

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
- `uvx ruff check src tests` passes.
- Documentation describes new user-visible behavior.
- No real trajectory, credential, proprietary code, or personal path was committed.
