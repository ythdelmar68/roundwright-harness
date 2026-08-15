"""One phase-neutral, plan-bound external-validation executor."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from roundwright_harness.capture import (
    BoundCaptureReceipt,
    CapturePlanReceipt,
    prepare_capture,
    record_capture,
    validate_capture_evidence,
    validate_capture_plan,
    verify_capture,
)

EXECUTOR_REQUEST_SCHEMA = "roundwright-harness-profile-executor-request/v1"
EXECUTOR_READINESS_SCHEMA = "roundwright-harness-profile-executor-readiness/v1"
EXECUTOR_RESULT_SCHEMA = "roundwright-harness-profile-executor-result/v1"
EXECUTOR_STATUS_SCHEMA = "roundwright-harness-profile-executor-status/v1"


class ExecutorError(ValueError):
    """The executor could not consume one immutable validation binding."""


class ExecutorState(Enum):
    UNPREPARED = "UNPREPARED"
    PREPARED = "PREPARED"
    PREFLIGHT_READY = "PREFLIGHT_READY"
    ARMED = "ARMED"
    EXECUTED = "EXECUTED"
    PROJECTED = "PROJECTED"
    SEALED = "SEALED"
    VERIFIED = "VERIFIED"
    STALE = "STALE"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class ProfileComponentIdentities:
    producer_identity: str
    exporter_identity: str
    comparator_identity: str


@dataclass(frozen=True)
class ExecutorRequest:
    capture_plan: dict[str, Any]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ExecutorRequest":
        if type(value) is not dict or set(value) != {"schema", "capture_plan"}:
            raise ExecutorError("executor request is incomplete")
        if value["schema"] != EXECUTOR_REQUEST_SCHEMA or type(value["capture_plan"]) is not dict:
            raise ExecutorError("executor request schema is unsupported")
        try:
            plan = validate_capture_plan(value["capture_plan"])
        except ValueError as error:
            raise ExecutorError("executor capture plan is invalid") from error
        return cls(plan)


@dataclass(frozen=True)
class ExecutorBinding:
    plan: CapturePlanReceipt
    components: ProfileComponentIdentities

    @property
    def profile(self) -> str:
        return self.plan.profile

    @property
    def case_id(self) -> str:
        return self.plan.case_id

    @property
    def candidate_sha(self) -> str:
        return self.plan.candidate_sha

    @property
    def ready_at(self) -> int:
        return self.plan.ready_at


@dataclass(frozen=True)
class ProfileExecution:
    """Opaque adapter result plus its exact mutation accounting."""

    value: object
    mutation_count: int = 0

    def __post_init__(self) -> None:
        if type(self.mutation_count) is not int or self.mutation_count < 0:
            raise ExecutorError("execution mutation count is invalid")


@dataclass(frozen=True)
class ProfileComparison:
    """Public-safe semantic result returned by a repository adapter."""

    status: Literal["pass", "fail"]
    result_identity: str

    def __post_init__(self) -> None:
        if self.status not in ("pass", "fail"):
            raise ExecutorError("comparison status is invalid")
        if (
            type(self.result_identity) is not str
            or len(self.result_identity) != 71
            or not self.result_identity.startswith("sha256:")
        ):
            raise ExecutorError("comparison identity is invalid")
        try:
            int(self.result_identity.removeprefix("sha256:"), 16)
        except ValueError as error:
            raise ExecutorError("comparison identity is invalid") from error


@runtime_checkable
class ProfileAdapter(Protocol):
    """Narrow product-owned behavior injected into the generic executor."""

    @property
    def component_identities(self) -> ProfileComponentIdentities: ...

    def validate(self, binding: ExecutorBinding) -> None: ...

    def execute(self, binding: ExecutorBinding) -> ProfileExecution: ...

    def project(
        self,
        binding: ExecutorBinding,
        execution: ProfileExecution,
    ) -> Mapping[str, Any]: ...

    def compare(
        self,
        binding: ExecutorBinding,
        evidence: Mapping[str, Any],
    ) -> ProfileComparison: ...


@dataclass(frozen=True)
class ExecutorReadinessReceipt:
    plan: CapturePlanReceipt
    components: ProfileComponentIdentities

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": EXECUTOR_READINESS_SCHEMA,
            "status": "ready",
            "state": ExecutorState.PREFLIGHT_READY.value,
            "plan_digest": self.plan.plan_digest,
            "profile": self.plan.profile,
            "case_id": self.plan.case_id,
            "candidate_sha": self.plan.candidate_sha,
            "ready_at": self.plan.ready_at,
            "producer_identity": self.components.producer_identity,
            "exporter_identity": self.components.exporter_identity,
            "comparator_identity": self.components.comparator_identity,
            "dispatch_count": 0,
            "record_count": 0,
            "verify_count": 0,
            "mutation_count": 0,
        }
        return {**core, "receipt_digest": _digest(core)}


@dataclass(frozen=True)
class ExecutorResultReceipt:
    readiness: ExecutorReadinessReceipt
    comparison: ProfileComparison
    capture: BoundCaptureReceipt
    mutation_count: int

    def as_dict(self) -> dict[str, object]:
        capture = self.capture.as_dict()
        readiness = self.readiness.as_dict()
        core: dict[str, object] = {
            "schema": EXECUTOR_RESULT_SCHEMA,
            "status": self.comparison.status,
            "state": ExecutorState.VERIFIED.value,
            "readiness_receipt_digest": readiness["receipt_digest"],
            "plan_digest": self.readiness.plan.plan_digest,
            "profile": self.readiness.plan.profile,
            "case_id": self.readiness.plan.case_id,
            "candidate_sha": self.readiness.plan.candidate_sha,
            "ready_at": self.readiness.plan.ready_at,
            "result_identity": self.comparison.result_identity,
            "bundle_digest": capture["bundle_digest"],
            "recording_receipt_digest": capture["recording_receipt_digest"],
            "retention_identity": capture["retention_identity"],
            "dispatch_count": 1,
            "record_count": 1,
            "verify_count": 1,
            "mutation_count": self.mutation_count,
        }
        return {**core, "receipt_digest": _digest(core)}


class ProfileExecutor:
    """Consume one request at most once through every validation stage."""

    def __init__(
        self,
        request_value: Mapping[str, Any],
        adapter: ProfileAdapter,
        store_root: Path,
    ) -> None:
        self._request_value = request_value
        self._adapter = adapter
        self._store_root = store_root
        self._request: ExecutorRequest | None = None
        self._readiness: ExecutorReadinessReceipt | None = None
        self.state = ExecutorState.UNPREPARED
        self.dispatch_count = 0
        self.record_count = 0
        self.verify_count = 0
        self.mutation_count = 0

    def _prepare_and_validate(self) -> ExecutorReadinessReceipt:
        if self.state is not ExecutorState.UNPREPARED:
            if self.state is ExecutorState.PREFLIGHT_READY and self._readiness is not None:
                return self._readiness
            raise ExecutorError("executor request has already been consumed")
        try:
            request = ExecutorRequest.parse(self._request_value)
            plan = prepare_capture(request.capture_plan)
            self.state = ExecutorState.PREPARED
            components = self._adapter.component_identities
            expected = ProfileComponentIdentities(
                request.capture_plan["producer_identity"],
                request.capture_plan["exporter_identity"],
                request.capture_plan["comparator_identity"],
            )
            if type(components) is not ProfileComponentIdentities or components != expected:
                raise ExecutorError("adapter components do not match the capture plan")
            binding = ExecutorBinding(plan, components)
            self._adapter.validate(binding)
            readiness = ExecutorReadinessReceipt(plan, components)
        except Exception:
            self.state = ExecutorState.STALE
            raise
        self._request = request
        self._readiness = readiness
        self.state = ExecutorState.PREFLIGHT_READY
        return readiness

    def validate_only(self) -> ExecutorReadinessReceipt:
        """Run the provider-free path without dispatch, record, or verify."""

        return self._prepare_and_validate()

    def execute(self, expected_readiness_digest: str) -> ExecutorResultReceipt:
        """Consume the exact validated binding once and verify its sealed result."""

        readiness = self._prepare_and_validate()
        if readiness.as_dict()["receipt_digest"] != expected_readiness_digest:
            self.state = ExecutorState.STALE
            raise ExecutorError("validated readiness binding has moved")
        assert self._request is not None
        binding = ExecutorBinding(readiness.plan, readiness.components)
        try:
            self.state = ExecutorState.ARMED
            execution = self._adapter.execute(binding)
            if type(execution) is not ProfileExecution:
                raise ExecutorError("adapter execution result is invalid")
            self.dispatch_count = 1
            self.mutation_count = execution.mutation_count
            self.state = ExecutorState.EXECUTED
            evidence = self._adapter.project(binding, execution)
            canonical_evidence = validate_capture_evidence(readiness.plan, evidence)
            self.state = ExecutorState.PROJECTED
            comparison = self._adapter.compare(binding, canonical_evidence)
            if type(comparison) is not ProfileComparison:
                raise ExecutorError("adapter comparison result is invalid")
            sealed = record_capture(
                self._request.capture_plan,
                canonical_evidence,
                self._store_root,
            )
            self.record_count = 1
            self.state = ExecutorState.SEALED
            verified = verify_capture(
                self._request.capture_plan,
                self._store_root,
                sealed.recording.bundle_digest,
            )
            self.verify_count = 1
            if verified != sealed:
                raise ExecutorError("sealed capture did not verify exactly")
        except Exception:
            self.state = ExecutorState.STALE
            raise
        self.state = ExecutorState.VERIFIED
        return ExecutorResultReceipt(readiness, comparison, verified, execution.mutation_count)


def run_profile_executor(
    mode: Literal["validate", "execute"],
    request_value: Mapping[str, Any],
    adapter: ProfileAdapter,
    store_root: Path,
    *,
    expected_readiness_digest: str | None = None,
) -> ExecutorReadinessReceipt | ExecutorResultReceipt:
    """The single public entrypoint for dry validation and one-shot execution."""

    executor = ProfileExecutor(request_value, adapter, store_root)
    if mode == "validate" and expected_readiness_digest is None:
        return executor.validate_only()
    if mode == "execute" and expected_readiness_digest is not None:
        return executor.execute(expected_readiness_digest)
    raise ExecutorError("executor mode arguments are invalid")
