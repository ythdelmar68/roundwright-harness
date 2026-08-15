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

A profile that needs product runtime state uses the v2 request and also exposes
`prepare_execution_context(preparation)`. The Harness gives that method an
immutable, product-defined JSON descriptor plus its digest. The adapter returns
one `ProfileExecutionContext`: a public content identity and an opaque in-memory
value. The Harness carries that value unchanged in `ExecutorBinding`; it never
interprets or serializes it.

The Harness never reads adapter private attributes or infers missing
constructors. Product code never owns Recorder storage or rewrites Harness
receipts.

## One immutable request

`roundwright-harness-profile-executor-request/v1` remains the context-free
contract and contains exactly:

- `schema`; and
- one closed `roundwright-harness-capture-plan/v1` object.

`roundwright-harness-profile-executor-request/v2` contains exactly:

- `schema`;
- the same closed capture plan; and
- one non-empty `execution_context` JSON object whose meaning belongs only to
  the repository adapter.

The v2 descriptor may name public product identities, policy/configuration
digests, and local-only resource references needed to materialize a bounded
capability. It must not contain credentials, tokens, raw provider output, or
hidden reasoning. The adapter resolves any actual provider or durable-store
objects in memory; the Harness includes only the descriptor digest and returned
context identity in v2 receipts.

The plan binds the exact profile, case, candidate, evidence time, producer,
exporter, comparator, Recorder, store, and observation identities. `validate`
and `execute` use the same command, request parser, and state machine. Validate
returns the matching v1 or v2 readiness receipt; execute must be given that
receipt's exact digest. A v2 receipt also binds the context-input digest and the
materialized context identity. It cannot substitute a second plan,
candidate-specific wrapper, context descriptor, or runtime context.

Within one executor invocation, validation, execution, projection, and
comparison receive the exact same `ExecutorBinding` and opaque context object.
The CLI's provider-free validation and one-shot execution are separate
processes, so each process materializes once; equality of both v2 context
identities is enforced by the readiness digest before dispatch.

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
The v2 request descriptor and opaque context value are never copied into a
receipt, evidence bundle, status output, or log by the Harness. Receipts do not
expose local paths, credentials, raw external payloads, provider prose,
transcripts, hidden reasoning, or owner reasoning. The Recorder store must be
an explicit durable location outside disposable candidate worktrees.
