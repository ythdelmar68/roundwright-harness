"""Command-line entrypoint with public-safe output."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable

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
from roundwright_harness.executor import (
    EXECUTOR_STATUS_SCHEMA,
    ExecutorError,
    ExecutorRequest,
    ProfileAdapter,
    ProfileExecutor,
)
from roundwright_harness.lifecycle import (
    LIFECYCLE_STATUS_SCHEMA,
    LifecycleLedgerError,
    append_lifecycle_event,
    prepare_lifecycle,
    seal_lifecycle,
    verify_lifecycle,
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


def lifecycle_operation(
    operation: str,
    *,
    store_root: Path,
    plan_path: Path | None = None,
    event_path: Path | None = None,
    ledger_digest: str | None = None,
) -> int:
    """Operate one generic append-only lifecycle window with safe output."""

    try:
        if operation == "verify" and ledger_digest is not None:
            payload = verify_lifecycle(store_root, ledger_digest).as_dict()
        elif plan_path is not None:
            plan = load_document(plan_path)
            if operation == "prepare":
                payload = prepare_lifecycle(plan, store_root).as_dict()
            elif operation == "append" and event_path is not None:
                payload = append_lifecycle_event(
                    plan,
                    load_document(event_path),
                    store_root,
                ).as_dict()
            elif operation == "seal":
                payload = seal_lifecycle(plan, store_root).as_dict()
            else:
                raise LifecycleLedgerError("lifecycle operation is incomplete")
        else:
            raise LifecycleLedgerError("lifecycle operation is incomplete")
    except (LifecycleLedgerError, RecordingError):
        payload = {
            "schema": LIFECYCLE_STATUS_SCHEMA,
            "gate": f"lifecycle-ledger-{operation}",
            "status": "blocked",
            "reason": "invalid-or-conflicting-lifecycle-binding",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    except OSError:
        payload = {
            "schema": LIFECYCLE_STATUS_SCHEMA,
            "gate": f"lifecycle-ledger-{operation}",
            "status": "blocked",
            "reason": "lifecycle-store-unavailable",
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def _load_profile_adapter(
    factory_spec: str,
    request: ExecutorRequest,
) -> ProfileAdapter:
    """Load one exact public factory without inspecting adapter internals."""

    if factory_spec.count(":") != 1:
        raise ExecutorError("adapter factory identity is invalid")
    module_name, attribute_name = factory_spec.split(":")
    if not module_name or not attribute_name or attribute_name.startswith("_"):
        raise ExecutorError("adapter factory identity is invalid")
    module = importlib.import_module(module_name)
    factory: Callable[[str], object] = getattr(module, attribute_name)
    adapter = factory(request.capture_plan["profile"])
    if not isinstance(adapter, ProfileAdapter):
        raise ExecutorError("adapter factory result is invalid")
    return adapter


def profile_operation(
    mode: str,
    *,
    request_path: Path,
    store_root: Path,
    adapter_factory: str,
    expected_readiness_digest: str | None,
) -> int:
    """Use one command path for provider-free validation and execution."""

    runner: ProfileExecutor | None = None
    try:
        request_value = load_document(request_path)
        request = ExecutorRequest.parse(request_value)
        adapter = _load_profile_adapter(adapter_factory, request)
        runner = ProfileExecutor(request_value, adapter, store_root)
        if mode == "validate" and expected_readiness_digest is None:
            payload = runner.validate_only().as_dict()
        elif mode == "execute" and expected_readiness_digest is not None:
            payload = runner.execute(expected_readiness_digest).as_dict()
        else:
            raise ExecutorError("executor mode arguments are invalid")
    except (
        ExecutorError,
        CapturePlanError,
        RecordingError,
        ImportError,
        AttributeError,
        TypeError,
        OSError,
    ):
        payload = {
            "schema": EXECUTOR_STATUS_SCHEMA,
            "gate": "profile-executor",
            "status": "blocked",
            "state": runner.state.value if runner is not None else "UNPREPARED",
            "dispatch_count": runner.dispatch_count if runner is not None else 0,
            "record_count": runner.record_count if runner is not None else 0,
            "verify_count": runner.verify_count if runner is not None else 0,
            "mutation_count": runner.mutation_count if runner is not None else 0,
            "reason": "invalid-or-conflicting-executor-binding",
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
    prepare_lifecycle_parser = subcommands.add_parser("prepare-lifecycle")
    prepare_lifecycle_parser.add_argument("--plan", type=Path, required=True)
    prepare_lifecycle_parser.add_argument("--store", type=Path, required=True)
    append_lifecycle_parser = subcommands.add_parser("append-lifecycle")
    append_lifecycle_parser.add_argument("--plan", type=Path, required=True)
    append_lifecycle_parser.add_argument("--event", type=Path, required=True)
    append_lifecycle_parser.add_argument("--store", type=Path, required=True)
    seal_lifecycle_parser = subcommands.add_parser("seal-lifecycle")
    seal_lifecycle_parser.add_argument("--plan", type=Path, required=True)
    seal_lifecycle_parser.add_argument("--store", type=Path, required=True)
    verify_lifecycle_parser = subcommands.add_parser("verify-lifecycle")
    verify_lifecycle_parser.add_argument("--store", type=Path, required=True)
    verify_lifecycle_parser.add_argument("--ledger-digest", required=True)
    profile_parser = subcommands.add_parser("run-profile")
    profile_parser.add_argument("--mode", choices=("validate", "execute"), required=True)
    profile_parser.add_argument("--request", type=Path, required=True)
    profile_parser.add_argument("--store", type=Path, required=True)
    profile_parser.add_argument("--adapter-factory", required=True)
    profile_parser.add_argument("--expected-readiness-digest")
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
    if arguments.command == "prepare-lifecycle":
        return lifecycle_operation(
            "prepare",
            plan_path=arguments.plan,
            store_root=arguments.store,
        )
    if arguments.command == "append-lifecycle":
        return lifecycle_operation(
            "append",
            plan_path=arguments.plan,
            event_path=arguments.event,
            store_root=arguments.store,
        )
    if arguments.command == "seal-lifecycle":
        return lifecycle_operation(
            "seal",
            plan_path=arguments.plan,
            store_root=arguments.store,
        )
    if arguments.command == "verify-lifecycle":
        return lifecycle_operation(
            "verify",
            store_root=arguments.store,
            ledger_digest=arguments.ledger_digest,
        )
    if arguments.command == "run-profile":
        return profile_operation(
            arguments.mode,
            request_path=arguments.request,
            store_root=arguments.store,
            adapter_factory=arguments.adapter_factory,
            expected_readiness_digest=arguments.expected_readiness_digest,
        )
    raise AssertionError("unreachable")
