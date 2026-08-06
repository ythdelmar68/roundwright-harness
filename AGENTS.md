# Repository instructions

This public repository is the phase-neutral qualification harness for
Roundwright and Roundlet. It is intended for repeated use in Phases 3 through
6 and later maintenance work.

## Safety and evidence

- Keep credentials, tokens, authentication caches, raw model responses, local
  absolute paths, private reasoning, and unredacted run artifacts out of Git.
- A live provider gate must be explicitly enabled. CI runs only hermetic and
  credential-free checks unless a separately reviewed workflow says otherwise.
- Emit only typed, owner-safe pass/block status and stable non-secret identity
  data. Do not print exception text returned by a provider.
- Treat `roundlet-forward-test` as a public, destructive-test-only target. Never
  point destructive fixtures at a production or unique-work repository.

## Python environment

- Manage this repository with uv. Keep `.venv` repo-local and untracked.
- Commit `uv.lock` and use `uv sync --locked` / `uv run --locked` for repeatable
  execution. Do not install project dependencies into global Python.
- A Roundwright checkout under test is supplied explicitly at runtime. Do not
  hard-code a developer machine path or add it as a lockfile dependency.

## Version control

- Use Conventional Commits in the form `<type>(<scope>): <description>`.
- Keep branches and commits focused on one logical change.
- Do not release, tag, publish a package, or widen live-test authority without
  explicit owner approval.
