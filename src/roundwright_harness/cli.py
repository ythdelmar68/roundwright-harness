"""Command-line entrypoint with public-safe output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from roundwright_harness.capture import (
    CAPTURE_STATUS_SCHEMA,
    CapturePlanError,
    prepare_capture,
    record_capture,
    verify_capture,
)
from roundwright_harness.recording import (
    STATUS_SCHEMA,
    RecordingError,
    load_document,
    record_document,
    verify_recording,
)

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


def record_shadow(*, input_path: Path, store_root: Path) -> int:
    """Record one public-safe case and emit only its typed receipt."""

    try:
        receipt = record_document(load_document(input_path), store_root)
    except RecordingError:
        payload = {
            "schema": STATUS_SCHEMA,
            "gate": "shadow-recorder",
            "status": "blocked",
            "reason": "invalid-or-unsafe-evidence",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    except OSError:
        payload = {
            "schema": STATUS_SCHEMA,
            "gate": "shadow-recorder",
            "status": "blocked",
            "reason": "recording-unavailable",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0


def verify_shadow(*, store_root: Path, bundle_digest: str) -> int:
    """Verify one retained case and emit only its typed receipt."""

    try:
        receipt = verify_recording(store_root, bundle_digest)
    except RecordingError:
        payload = {
            "schema": STATUS_SCHEMA,
            "gate": "shadow-recorder-read-back",
            "status": "blocked",
            "reason": "invalid-or-conflicting-recording",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    except OSError:
        payload = {
            "schema": STATUS_SCHEMA,
            "gate": "shadow-recorder-read-back",
            "status": "blocked",
            "reason": "recording-unavailable",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(receipt.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0


def capture_operation(
    operation: str,
    *,
    plan_path: Path,
    input_path: Path | None = None,
    store_root: Path | None = None,
    bundle_digest: str | None = None,
) -> int:
    """Run one plan-bound operation with typed, path-free failure output."""

    try:
        plan = load_document(plan_path)
        if operation == "prepare":
            payload = prepare_capture(plan).as_dict()
        elif operation == "record" and input_path is not None and store_root is not None:
            payload = record_capture(plan, load_document(input_path), store_root).as_dict()
        elif operation == "verify" and store_root is not None and bundle_digest is not None:
            payload = verify_capture(plan, store_root, bundle_digest).as_dict()
        else:
            raise CapturePlanError("capture operation is incomplete")
    except (CapturePlanError, RecordingError):
        payload = {
            "schema": CAPTURE_STATUS_SCHEMA,
            "gate": f"shadow-capture-{operation}",
            "status": "blocked",
            "reason": "invalid-or-conflicting-capture-binding",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    except OSError:
        payload = {
            "schema": CAPTURE_STATUS_SCHEMA,
            "gate": f"shadow-capture-{operation}",
            "status": "blocked",
            "reason": "capture-unavailable",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="roundwright-harness")
    subcommands = value.add_subparsers(dest="command", required=True)
    doctor_parser = subcommands.add_parser("doctor")
    doctor_parser.add_argument("--require-roundwright", action="store_true")
    recorder = subcommands.add_parser("record-shadow")
    recorder.add_argument("--input", type=Path, required=True)
    recorder.add_argument("--store", type=Path, required=True)
    verifier = subcommands.add_parser("verify-shadow")
    verifier.add_argument("--store", type=Path, required=True)
    verifier.add_argument("--bundle-digest", required=True)
    prepare_capture_parser = subcommands.add_parser("prepare-capture")
    prepare_capture_parser.add_argument("--plan", type=Path, required=True)
    record_capture_parser = subcommands.add_parser("record-capture")
    record_capture_parser.add_argument("--plan", type=Path, required=True)
    record_capture_parser.add_argument("--input", type=Path, required=True)
    record_capture_parser.add_argument("--store", type=Path, required=True)
    verify_capture_parser = subcommands.add_parser("verify-capture")
    verify_capture_parser.add_argument("--plan", type=Path, required=True)
    verify_capture_parser.add_argument("--store", type=Path, required=True)
    verify_capture_parser.add_argument("--bundle-digest", required=True)
    return value


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "doctor":
        return doctor(require_roundwright=arguments.require_roundwright)
    if arguments.command == "record-shadow":
        return record_shadow(input_path=arguments.input, store_root=arguments.store)
    if arguments.command == "verify-shadow":
        return verify_shadow(
            store_root=arguments.store,
            bundle_digest=arguments.bundle_digest,
        )
    if arguments.command == "prepare-capture":
        return capture_operation("prepare", plan_path=arguments.plan)
    if arguments.command == "record-capture":
        return capture_operation(
            "record",
            plan_path=arguments.plan,
            input_path=arguments.input,
            store_root=arguments.store,
        )
    if arguments.command == "verify-capture":
        return capture_operation(
            "verify",
            plan_path=arguments.plan,
            store_root=arguments.store,
            bundle_digest=arguments.bundle_digest,
        )
    raise AssertionError("unreachable")
