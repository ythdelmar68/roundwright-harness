# Phase-neutral ownership

The harness owns reusable execution environment and evidence plumbing. The
Roundwright repository continues to own product behavior, lifecycle policy,
phase gates, and promotion decisions.

| Phase | Reuse in this repository | Remains in Roundwright |
| --- | --- | --- |
| 3 | Native Codex SDK qualification, exact-candidate preflight, typed read-only evidence, content-addressed Shadow recording, and opt-in generic lifecycle event sealing | Provider-health contract, versioned Shadow schemas/profiles/comparator, lifecycle-event projection, leaf acceptance and merge gate |
| 4 | Controlled canary runners, cross-environment matrix, disposable target adapters | Canary authority, dispatch policy, environment acceptance thresholds |
| 5 | Long-run operational probes, retention/migration fixtures, promotion evidence capture | Scanner/daemon lifecycle, retention semantics, promotion decision |
| 6 | Release-readiness and installation smoke environments | Versioning, packaging, release approval and publication |

New gates should be added here when they need external credentials, platform
matrix execution, a disposable remote, or dependencies that should not be
installed in the Roundwright source checkout. Pure product contracts and
hermetic unit tests stay in Roundwright.

The repository name intentionally contains no phase number so later phases do
not need another environment migration or duplicate authentication setup.
