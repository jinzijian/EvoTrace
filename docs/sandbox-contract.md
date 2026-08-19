# Sandbox contract

ScaleVerifier separates trusted host orchestration from untrusted agent work. The host collector may read local
history and Git data because reconstruction requires both. An autonomous curator, environment builder, verifier
writer, or candidate agent must never receive a writable host checkout.

This document is a normative contract for the planned ScaleVerifier agent runtime. The V0.2 builder emits the
same policy into every bundle as `sandbox-policy.json`. V0.2 does not yet launch the autonomous runtime and rejects
legacy host-side `benchmark --agent` execution.

## Allowed host operations

The trusted, deterministic CLI may:

- read configured Claude Code and Codex history files;
- read Git objects, refs, metadata, tracked files, and selected untracked files;
- write normalized records only under a new or existing ScaleVerifier store;
- create a new bundle or unique run directory;
- ask the Docker daemon to build and start a task image;
- copy validated output from the container into its unique run directory.

It must not modify a source repository, source transcript, Claude Code state, or Codex state during import, mining,
or build.

## Container input

The task source tree enters the image as a `git archive`-derived tarball plus separately recorded initial state.
The Dockerfile uses `COPY`; runtime must not bind-mount the original repository. Task text, verifier configuration,
and sandbox policy are copied into the image as immutable inputs.

The agent receives the same task code that the eval exposes, but only as its disposable `/workspace` copy. It may
edit, move, or delete anything in that copy without affecting the host repository.

## Required runtime controls

A conforming orchestrator must enforce all of the following:

- no host filesystem bind mounts;
- no Docker or container-runtime socket;
- no `--privileged`, host PID, host IPC, host network, or device passthrough;
- a non-root UID/GID;
- `no-new-privileges` and all Linux capabilities dropped;
- CPU, memory, process-count, wall-clock, and output-size limits;
- network disabled unless an explicit policy names a specific read-only adapter;
- no inherited host environment or credential forwarding;
- a fresh container for every attempt;
- exact-container cleanup by the trusted orchestrator only.

The generated Dockerfile runs as UID/GID 65532. A future orchestrator should use flags equivalent to:

```text
--network none
--cap-drop ALL
--security-opt no-new-privileges
--pids-limit <bounded>
--memory <bounded>
--cpus <bounded>
--user 65532:65532
```

These controls reduce risk; Docker is not a perfect security boundary. High-sensitivity deployments should use an
additional VM, microVM, or organization-approved sandbox runtime.

## Internal access

"The same internal access" must mean access to behavior needed to reconstruct and verify the task, not possession
of the original user's write credentials. Supported patterns are:

1. deterministic mocks generated from schemas and sanitized fixtures;
2. record/replay responses captured with explicit permission and secret redaction;
3. narrowly scoped read-only adapters with an allowlist, audit log, rate limits, and revocable task token.

Production write credentials, broad cloud credentials, SSH agent forwarding, browser sessions, and personal API
keys are forbidden. If the task cannot be evaluated without mutation, the environment must emulate that mutation
inside the sandbox.

## Output promotion

The agent never selects an arbitrary host destination. The orchestrator creates a unique run directory before the
container starts. It may copy out only allowlisted artifacts such as:

- the final patch;
- verifier report and test logs;
- generated mock definitions;
- a proposed verifier patch;
- a machine-readable execution manifest.

Promotion occurs only after archive-path validation, size limits, secret scanning, and verifier-policy checks.
Existing host files are never overwritten or deleted.

## Threat model

All of the following are untrusted input:

- transcript messages and tool output;
- repository source, build scripts, lock files, and tests;
- generated Dockerfiles and verifier commands;
- instructions embedded in issues, docs, fixtures, or source comments;
- model-generated mocks and verifier code.

In particular, a transcript or repository may contain prompt injection instructing an agent to read credentials,
mount host paths, weaken the verifier, or exfiltrate data. Such instructions do not override this contract.

## Verifier separation

The builder agent may propose a verifier, but it cannot approve its own verifier. A trusted validation stage must:

- record verifier provenance;
- demonstrate failure on the reconstructed base state when appropriate;
- demonstrate success on the observed reference when available;
- test at least one known-wrong or mutation candidate;
- reject changes to protected tests or verifier policy;
- preserve all failures and uncertainty in the output manifest.

Until these checks are implemented, a generated verifier remains a candidate that requires human review.
