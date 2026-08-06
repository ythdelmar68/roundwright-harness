# roundwright-harness

Phase-neutral qualification harness for Roundwright and Roundlet integration gates.

This is a public repository so its credential-free GitHub Actions checks can
run on the public-repository allowance. Public visibility does **not** authorize
publishing credentials, Codex transcripts, raw run artifacts, or private local
paths.

## What lives where

- `uv` is the only user-level Python tool required on a workstation.
- This repository owns its Python 3.12 `.venv`, `openai-codex` SDK, pinned Codex
  runtime, test tools, and `uv.lock`.
- A Roundwright checkout is supplied as an explicit, temporary uv overlay. It is
  not hard-coded into this repository's dependency lock.
- [`roundlet-forward-test`](https://github.com/ythdelmar68/roundlet-forward-test)
  remains the public disposable GitHub target. It does not own this Python
  environment.

That separation keeps the test target disposable, the harness reusable, and
the workstation's global Python untouched.

## Credential-free setup

```powershell
uv sync --locked
uv run --locked roundwright-harness doctor
uv run --locked pytest
```

To verify a sibling Roundwright checkout without installing it permanently:

```powershell
uv run --locked --with-editable ..\roundwright `
  roundwright-harness doctor --require-roundwright
```

The `doctor` gate reports only package versions and pass/block state. It does
not start Codex or inspect an authentication cache.

## Live provider gate

The live gate is separate and opt-in. It uses the Python Codex SDK's pinned
runtime, opens an ephemeral read-only thread, denies approvals, and requests a
fixed structured readiness response. The adapter returns only Roundwright's
typed outcome; provider text and exceptions are never emitted as evidence.

Use `roundwright_harness.native:native_factory` as
`ROUNDWRIGHT_LIVE_PROVIDER_FACTORY`. See [Live gate](docs/live-gate.md) for the
exact sequence and stop conditions. If `doctor` passes but the live gate reports
`auth-missing`, the repository environment is complete and only an interactive
Codex login remains.

## Why the name is phase-neutral

The same environment is expected to qualify more than the current Phase 3
provider-health leaf. Phase 4 needs controlled canary and cross-environment
evidence; Phase 5 needs operational, migration, retention, and promotion
evidence; Phase 6 needs release-readiness evidence. The detailed ownership map
is in [Phase use](docs/phase-use.md).
