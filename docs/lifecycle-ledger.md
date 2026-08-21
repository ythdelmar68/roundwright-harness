# Lifecycle observation ledger

The lifecycle ledger is a phase-neutral, opt-in evidence boundary for short
events that do not survive task cleanup. It records generic Worker and
Supervisor transition facts; it does not know a product profile, decide a gate,
call a provider, or mutate GitHub.

## Arm before the first event

The repository creates one closed `roundwright-harness-lifecycle-plan/v1`
document and persists it with `prepare-lifecycle`. The plan binds:

- opaque window, repository, producer, store, and capture-plan identities;
- the exact candidate and immutable `ready_at`;
- one formal review epoch, round, and mode; and
- the only supported lifecycle event schema.

Preparation writes the plan and its path-free receipt once. A changed plan
cannot reuse the same window. If an event happened before preparation, open a
fresh window; never reconstruct it.

## Append and read back

Each `roundwright-harness-lifecycle-event/v1` document contains only closed,
public-safe fields: bound identities, sequence/time, Worker or Supervisor role,
task and attempt identities, formal review tuple, transition, disposition,
accepted-result flag, optional candidate movement, predecessor digest, and
bounded artifact digests.

`append-lifecycle` requires the next exact sequence and predecessor, writes the
event and receipt once, then re-reads the complete chain before returning. The
transition vocabulary keeps attempt start/completion, cancellation,
invalid-context, PASS/FINDINGS, accepted/unaccepted result, candidate movement,
and formal-round advancement distinct. Raw provider output, credentials,
private paths, transcripts, hidden reasoning, and arbitrary extension fields
are outside the schema.

## Seal and verify

`seal-lifecycle` creates one content-addressed bundle containing the armed plan,
ordered events, receipts, and hash-chain manifest. It writes an immutable seal
marker; later appends fail. `verify-lifecycle` recomputes the plan, every event
and entry digest, the manifest, bundle, receipt, retention identity, and seal
marker.

```powershell
uv run --locked roundwright-harness prepare-lifecycle `
  --plan .\lifecycle-plan.json --store .\.harness-output\lifecycle

uv run --locked roundwright-harness append-lifecycle `
  --plan .\lifecycle-plan.json --event .\event-0000.json `
  --store .\.harness-output\lifecycle

uv run --locked roundwright-harness seal-lifecycle `
  --plan .\lifecycle-plan.json --store .\.harness-output\lifecycle

uv run --locked roundwright-harness verify-lifecycle `
  --store .\.harness-output\lifecycle `
  --ledger-digest sha256:<exact-ledger-digest>
```

The emitted receipts contain no local path. A product repository supplies the
adapter that maps the verified generic event chain into its own profile and
comparator. Non-empty semantic differences are therefore a product-owned gate,
not something the Harness can normalize away.
