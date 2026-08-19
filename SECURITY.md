# Security and privacy

ScaleVerifier processes source code, agent conversations, command output, and local file state. Treat this data as
sensitive.

## Reporting a vulnerability

Please use GitHub's private security advisory flow for this repository. Do not open a public issue containing a
working exploit, private trajectory, credential, or proprietary source code.

## Local-first behavior

The open-source CLI does not upload sessions, bundles, source code, or telemetry. Imported raw history is read in
place and is not copied into ScaleVerifier storage.

Best-effort redaction covers several common token and credential formats. It is not a data-loss-prevention system
and must not be treated as a guarantee.

## Bundle contents

A compiled bundle may contain:

- every tracked file present at the base commit;
- the initial tracked dirty patch;
- selected initial untracked files;
- the observed final patch;
- recovered task text and verifier commands.

Git-ignored files, common `.env` names, and common private-key suffixes are excluded from untracked snapshots.
Tracked secrets and credentials embedded in patches can still be present. Inspect the archive, patches, task, and
verifier before moving a bundle outside its original trust boundary.

## Executing untrusted content

`setup.sh`, `verifier.py`, `scaleverifier verify`, and `scaleverifier benchmark` can execute repository or bundle
commands. A malicious task bundle can run arbitrary code with the current user's permissions. Only execute bundles
you trust, preferably in a disposable container or virtual machine.

Autonomous agent execution on the host is prohibited by the V0.2 sandbox contract. `benchmark --agent` is rejected
rather than running a supplied agent command against a host-side checkout. Generated bundles include a non-root
Dockerfile and `sandbox-policy.json`; the planned orchestrator must additionally enforce no host bind mounts, no
Docker socket, no privileged mode, no inherited credentials, network disabled by default, dropped capabilities,
and bounded resources. See [`docs/sandbox-contract.md`](docs/sandbox-contract.md).

`scaleverifier verify` and candidate scoring remain explicit host-side operations and execute verifier commands.
They should be used only with a bundle and checkout the operator trusts.

## Recommended sharing checklist

1. Inspect `task.md`, `task.json`, `verifier.json`, and both patch files.
2. List and inspect `environment/base.tar.gz` and `untracked-initial.tar.gz`.
3. Run an organization-approved secret scanner over the extracted bundle.
4. Confirm code ownership and license provenance.
5. Confirm that task text and paths contain no personal or customer information.
6. Share only after an explicit human approval.
