from __future__ import annotations

from roundwright_harness import native


def test_native_module_has_stable_public_factory() -> None:
    assert callable(native.native_factory)
    assert native._digest({"role": "worker"}).startswith("sha256:")
    assert len(native._digest({"role": "worker"})) == 71
