"""Command-line entrypoint with public-safe output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from importlib.metadata import PackageNotFoundError, version

SCHEMA = "roundwright-harness/v1"


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def doctor(*, require_roundwright: bool) -> int:
    sdk_version = _package_version("openai-codex")
    runtime_version = _package_version("openai-codex-cli-bin")
    roundwright_available = importlib.util.find_spec("roundwright") is not None
    ready = (
        sys.version_info[:2] == (3, 12)
        and sdk_version is not None
        and runtime_version is not None
        and (roundwright_available or not require_roundwright)
    )
    payload = {
        "schema": SCHEMA,
        "gate": "doctor",
        "status": "pass" if ready else "blocked",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "codex_sdk": sdk_version,
        "codex_runtime": runtime_version,
        "roundwright_available": roundwright_available,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if ready else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="roundwright-harness")
    subcommands = value.add_subparsers(dest="command", required=True)
    doctor_parser = subcommands.add_parser("doctor")
    doctor_parser.add_argument("--require-roundwright", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "doctor":
        return doctor(require_roundwright=arguments.require_roundwright)
    raise AssertionError("unreachable")
