# Versioned profile executor

## Ownership boundary

The Harness provides one phase-neutral executor. It does not know a product's
issue phases, provider types, profile payload, comparator rules, repository
authority, or mutation target. A repository supplies those semantics through
the public `ProfileAdapter` protocol and one explicit `module:factory` entrypoint.

The factory receives the exact profile identity from the already parsed
request. Its adapter exposes three immutable component identities and four
operations:

- `validate(binding)` performs provider-free readiness checks;
- `execute(binding)` performs at most one bounded external action and reports
  the exact mutation count;
- `project(binding, execution)` produces one public-safe typed evidence case;
- `compare(binding, evidence)` returns one typed semantic status and result
  identity.

The Harness never reads adapter private attributes or infers missing
constructors. Product code never owns Recorder storage or rewrites Harness
receipts.

## One immutable request

`roundwright-harness-profile-executor-request/v1` contains exactly:

- `schema`; and
- one closed `roundwright-harness-capture-plan/v1` object.

The plan binds the exact profile, case, candidate, evidence time, producer,
exporter, comparator, Recorder, store, and observation identities. `validate`
and `execute` use the same command, request parser, and state machine. Validate
returns `roundwright-harness-profile-executor-readiness/v1`; execute must be
given that receipt's exact digest. It cannot substitute a second plan or
candidate-specific wrapper.

The internal transition is:

```text
UNPREPARED -> PREPARED -> PREFLIGHT_READY -> ARMED
           -> EXECUTED -> PROJECTED -> SEALED -> VERIFIED
```

Any failure or movement makes that executor instance `STALE`. A completed or
stale instance cannot dispatch again. A fresh request and observation are
required.

## Safety and evidence

Provider-free failure occurs before `ARMED` and therefore reports zero
dispatches, records, verifications, and mutations. A successful execution
reports exactly one dispatch, one content-addressed append-only record, and one
verified read-back. Mutation accounting comes from the adapter result; the
Harness does not infer it.

The adapter must project public-safe evidence before the Recorder is called.
Malformed projection blocks before sealing. An interrupted or partial seal can
never produce a `VERIFIED` result. Verification recomputes the retained bundle
against the original capture plan. Historical replay and comparison always use
the plan's immutable `ready_at`, never the current clock.

Receipts contain only public identities, status, counts, and content digests.
They do not expose local paths, credentials, raw external payloads, provider
prose, transcripts, hidden reasoning, or owner reasoning. The store must be an
explicit durable location outside disposable candidate worktrees.
