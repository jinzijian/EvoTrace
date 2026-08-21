# EvoTrace task quality standard

This document separates trajectory value, task reconstructability, verifier validity, and empirical difficulty.
No single score may stand in for all four.

## 1. Unit of work: episode, not session

A local Claude Code or Codex session is a source container. A task candidate is a bounded episode inside that
session. Every episode must preserve:

- parent session id;
- inclusive source-event range and timestamps when available;
- boundary reasons;
- candidate user intents and the selected task intent;
- effective action counts and excluded-noise counts;
- repository and Git anchors observed inside or around the range.

Waiting, polling, status checks, planning metadata, and repeated progress queries do not count as coding work.
Short user follow-ups remain in the active episode unless repository state or intent changes materially.

## 2. Independent quality axes

### Value

Evidence that the experience contains reusable supervision: human correction, discarded work, failure recovery,
successful implementation, or grounded technical decisions.

### Effective length

Measured from meaningful user turns, repository reads, execution commands, code edits, test commands, and
failure-to-recovery cycles. Raw transcript bytes, wall time, `wait`, polling, and status traffic are reported but
never increase effective length.

### Behavioral complexity

Measured from independent requirements, touched components, reference patch breadth, behavioral test breadth,
compatibility or error constraints, and execution/recovery depth. Patch size alone is insufficient.

### Reconstructability

One of:

- `direct`: task boundary, repository base, reference state, and behavioral verifier evidence are recoverable;
- `derived_seed`: a long, complex episode has enough grounded evidence for a new task to be constructed, but the
  original reference or environment boundary is incomplete;
- `preference_only`: useful correction or recovery supervision exists without a defensible execution world;
- `reject`: insufficient coding evidence, unsafe content, or no coherent task boundary.

### Empirical difficulty

Measured only by fresh solver attempts against an unchanged task and verifier version. Structural estimates may
prioritize tasks but may not claim agent difficulty.

## 3. State machine

```text
session
  -> episode_candidate
      -> direct | derived_seed | preference_only | reject

direct
  -> bundle_generated
      -> verifier_validated
          -> self_play_calibrated | too_easy | too_hard

derived_seed or too_easy
  -> hardened_child_generated
      -> verifier_validated
          -> self_play_calibrated | needs_further_hardening | too_hard
```

`Verified` means only that a conforming Docker gate rejected the base and accepted a known-good reference for the
exact bundle digest. `Training-ready` additionally requires a coherent episode, direct reconstructability, a
tamper-resistant verifier gate, and empirical difficulty in the target curriculum bucket.

## 4. Direct task gate

A direct executable task requires all of:

1. a bounded episode with a standalone task intent;
2. an available repository base and initial state;
3. a non-empty reference patch or reference snapshot;
4. at least one behavioral verifier command observed or independently justified;
5. no unresolved sensitive-content gate;
6. sufficient effective length or behavioral complexity for the intended training bucket.

The compiler may preserve a simpler direct task as a `hardening_seed`; it must not present it as training-ready.

## 5. Verifier gate

A training verifier must preserve evidence for:

1. behavioral failure on the base state;
2. success on the reference state;
3. rejection of at least one known-wrong or mutation candidate;
4. protected-test and verifier-policy integrity;
5. exact bundle digest and immutable run provenance;
6. no network, host mounts, Docker socket, privilege, or host credentials during scoring.

Generated verifier code is a candidate until every required gate passes.

## 6. Empirical difficulty gate

The default bucket is two passes in five independent attempts. Each attempt receives a fresh task-only workspace;
the reference and hidden verifier fixtures remain withheld. All candidate patches are scored in Docker.

- exactly target passes: `self_play_calibrated`;
- fewer passes: add only provenance-backed hints, otherwise `too_hard`;
- more passes: remove hints first; a stronger verifier must still accept the reference and reject known-wrong
  behavior;
- correct reference-equivalent solutions are never rejected merely to hit the target;
- exhausting legitimate changes yields `too_easy` and triggers child-task hardening, not score manipulation.

## 7. Hardened child gate

A hardened child must be derived without mutating its parent. Its base is the solved parent state, and lineage binds
both parent and child digests. The child task must specify at least two independently testable behavioral
requirements, including backward compatibility or edge/error behavior.

Reference implementation changes and hidden verifier fixtures remain separate. Future solvers see neither. The
child is not training-ready until the complete verifier and empirical-difficulty gates pass.

## 8. Auditability and privacy

All heuristics and model decisions produce machine-readable evidence and gaps. Model use is explicit opt-in. Source
transcripts and repositories are never modified. External-path access, secret detection, reconstruction ambiguity,
and failed gates remain visible and prevent certification.
