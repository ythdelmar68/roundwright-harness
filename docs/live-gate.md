# Live provider gate

## Preconditions

1. The harness and Roundwright candidate checkouts are clean and at the exact
   commits recorded by the orchestrator. A historical harness commit remains
   historical evidence only; a finding against it cannot be waived by reusing
   the same pin.
2. `uv sync --locked` and the credential-free `doctor` gate pass.
3. The operator explicitly enables the live gate.
4. The selected target is disposable. Any later mutation test must use the
   public `roundlet-forward-test` repository, never a production repository.

The current provider-health probe itself is read-only and does not use the
forward-test repository.

## Stable classification boundary

The adapter does not search exception or provider message text. It uses only
SDK exception types, structured turn error information, stable HTTP status
fields exposed by those structured variants, account presence, and the SDK's
validated model catalog.

- No required account is `AUTH_MISSING`.
- Structured unauthorized with an existing account is `AUTH_EXPIRED`.
- A structured usage limit is `QUOTA_OR_RATE_LIMIT`.
- A structured connection, overload, or server failure is
  `TRANSPORT_OR_PROVIDER_OUTAGE`.
- Missing structured evidence is `UNKNOWN`, even when human-readable text
  happens to contain words such as `unauthorized`, `quota`, or `model`.
- Unsupported model/effort selections are detected from the factual catalog;
  the harness does not manufacture a model/effort Cartesian product.
- If the stable high-level SDK returns only a partial catalog, the factory
  fails closed instead of presenting that page as the complete capability set.

This boundary is intentionally conservative. A new classification requires a
reviewed stable SDK field and regression coverage before a new exact harness
commit can be selected for qualification.

## PowerShell sequence

Run this from the exact Roundwright candidate checkout. Set `$harnessRoot` to
your harness checkout; it is a local operator value and must not be committed.

```powershell
$harnessRoot = "..\roundwright-harness"
$candidateSha = git rev-parse HEAD

$env:ROUNDWRIGHT_RUN_LIVE_PROVIDER_HEALTH = "1"
$env:ROUNDWRIGHT_LIVE_PROVIDER_FACTORY = `
  "roundwright_harness.native:native_factory"
$env:ROUNDWRIGHT_CONTRACT_COMMIT = $candidateSha
$env:ROUNDWRIGHT_CANDIDATE_SHA = $candidateSha
$env:ROUNDWRIGHT_SHADOW_CASE_ID = "provider-health-live"

uv run --project $harnessRoot --locked --with-editable . `
  python -m tests.live_provider_health
```

The orchestrator may bind `ROUNDWRIGHT_CONTRACT_COMMIT` to a different reviewed
commit when its immutable run contract requires that distinction. Do not guess
or silently replace that value.

## Interpreting the result

- Exit `0`: all configured role/profile selections produced fresh, typed,
  owner-safe receipts. Continue with Shadow replay and exact-candidate gates.
- Exit `1`: the live provider qualification is blocked. Keep the pull request
  draft and use the typed failure classification to decide whether login,
  capacity, model availability, SDK compatibility, or transport needs repair.
- Exit `2`: the explicit enable flag is absent. No provider call was attempted.

Never copy raw model text, exception messages, authentication state, or local
paths into GitHub. Review-limit exhaustion cannot turn a blocked live or Shadow
gate into a pass.
