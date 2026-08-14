# Shadow evidence Recorder

## Boundary

The Recorder is reusable evidence plumbing. It accepts a producer-created
`roundwright-shadow-case/v2` JSON object, validates its public envelope, seals
canonical bytes once, and returns a receipt. Roundwright owns profile semantics,
the comparator, mismatch classification, and the decision to pass or block a
gate. Roundlet may select and orchestrate the tool but does not need to
understand Shadow or retain an envelope for repositories that do not declare
one.

The required top-level identities are:

- `schema`: exactly `roundwright-shadow-case/v2`;
- `profile`: a versioned `roundwright-shadow-profile/<name>/vN` identity;
- `ready_at`: the producer's non-negative integer capture time;
- `case_id`: one stable public-safe identifier; and
- `candidate_sha`: one exact lowercase 40-character commit SHA.

Roundwright's selected profile defines the remaining typed fields. The Recorder
does not compare model prose or infer missing lifecycle events.

## Immutable capture plan

Ephemeral observations use `roundwright-harness-capture-plan/v1`. The plan is
prepared before the selected profile's arm-before boundary and contains exactly
the profile, case, candidate, `ready_at`, producer, exporter, comparator,
Recorder, store, and observation identities. The six component identities are
public-safe SHA-256 values; the Harness returns one canonical plan digest.

The provider or producer receives only the already-prepared public-safe binding.
The final case includes `capture_plan_digest`. `record-capture` requires the
case's profile, case, candidate, capture time, and plan digest to match before
writing anything. `verify-capture` independently reloads the sealed case and
recomputes the same plan binding. A changed candidate or any other identity
requires a new plan and a fresh observation; no stage may patch a prior plan.

This bridge does not invoke a provider or interpret a profile. Repository-owned
code remains responsible for turning the plan into a typed dispatch and for
deciding whether the typed result matches.

## Safety and retention

Input containing credentials, tokens, secrets, raw payloads/logs/provider
prose, transcripts, private paths, hidden reasoning, or owner reasoning is
rejected. The Recorder does not silently retain a private source alongside a
redacted projection. Producers must construct the public-safe typed envelope
before invocation.

The store contains one canonical bundle and one receipt named by the bundle
SHA-256. Existing identical bytes are idempotent. Existing different bytes at
the same identity fail closed and are never overwritten. The receipt exposes
only schema/profile/case/candidate/capture-time identities and manifest,
evidence, bundle, retention, and receipt digests. It never exposes the local
input or store path.

Use storage outside product Git that survives issue-worktree and Roundlet
cleanup. `.harness-output/` is ignored for bounded local qualification, but a
selected gate remains responsible for an exact retained store identity and
retention policy. Do not commit real recordings to Roundwright or Harness.

## Invocation

Prepare and consume a plan-bound capture:

```powershell
uv run --locked roundwright-harness prepare-capture --plan .\plan.json

uv run --locked roundwright-harness record-capture `
  --plan .\plan.json `
  --input .\case.json `
  --store .\.harness-output\shadow

uv run --locked roundwright-harness verify-capture `
  --plan .\plan.json `
  --store .\.harness-output\shadow `
  --bundle-digest sha256:<exact-bundle-digest>
```

Each command emits one typed, path-free receipt or blocked status. The original
unbound Recorder commands remain available for compatible retained workflows:

```powershell
uv run --locked roundwright-harness record-shadow `
  --input .\case.json `
  --store .\.harness-output\shadow
```

Exit `0` emits one sealed receipt. Exit `1` emits one typed, path-free blocked
status without exception text. No provider, GitHub, Git, forward-test, or
repository mutation is performed.

Read back the retained bundle before replay or recovery:

```powershell
uv run --locked roundwright-harness verify-shadow `
  --store .\.harness-output\shadow `
  --bundle-digest sha256:<exact-bundle-digest>
```

Verification recomputes the bundle address, canonical envelope, manifest,
manifest digest, public receipt, receipt digest, and retention identity. A
missing, modified, non-canonical, symlinked, or conflicting artifact blocks
without exposing its path or contents.
