"""Immutable, phase-neutral capture-plan binding for Shadow recording."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from roundwright_harness.recording import (
    RecordingReceipt,
    load_verified_document,
    record_document,
    validate_document,
)

CAPTURE_PLAN_SCHEMA = "roundwright-harness-capture-plan/v1"
CAPTURE_PLAN_RECEIPT_SCHEMA = "roundwright-harness-capture-plan-receipt/v1"
BOUND_CAPTURE_RECEIPT_SCHEMA = "roundwright-harness-bound-capture-receipt/v1"
CAPTURE_STATUS_SCHEMA = "roundwright-harness-capture-status/v1"

_PROFILE = re.compile(r"roundwright-shadow-profile/[a-z0-9-]+/v[1-9][0-9]*")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PLAN_FIELDS = {
    "schema",
    "profile",
    "ready_at",
    "case_id",
    "candidate_sha",
    "producer_identity",
    "exporter_identity",
    "comparator_identity",
    "recorder_identity",
    "store_identity",
    "observation_identity",
}


class CapturePlanError(ValueError):
    """The plan and evidence cannot form one immutable capture."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def validate_capture_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one closed canonical plan with no path or provider material."""

    if type(value) is not dict or set(value) != _PLAN_FIELDS:
        raise CapturePlanError("capture plan is incomplete")
    if value["schema"] != CAPTURE_PLAN_SCHEMA:
        raise CapturePlanError("capture plan schema is unsupported")
    if type(value["profile"]) is not str or _PROFILE.fullmatch(value["profile"]) is None:
        raise CapturePlanError("capture plan profile is invalid")
    if type(value["ready_at"]) is not int or value["ready_at"] < 0:
        raise CapturePlanError("capture plan time is invalid")
    if type(value["case_id"]) is not str or _IDENTIFIER.fullmatch(value["case_id"]) is None:
        raise CapturePlanError("capture plan case is invalid")
    if type(value["candidate_sha"]) is not str or _SHA.fullmatch(value["candidate_sha"]) is None:
        raise CapturePlanError("capture plan candidate is invalid")
    identities = _PLAN_FIELDS - {"schema", "profile", "ready_at", "case_id", "candidate_sha"}
    if any(type(value[name]) is not str or _DIGEST.fullmatch(value[name]) is None for name in identities):
        raise CapturePlanError("capture plan identity is invalid")
    return json.loads(_canonical_bytes(value))


@dataclass(frozen=True)
class CapturePlanReceipt:
    plan_digest: str
    profile: str
    case_id: str
    candidate_sha: str
    ready_at: int

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": CAPTURE_PLAN_RECEIPT_SCHEMA,
            "status": "ready",
            "plan_digest": self.plan_digest,
            "profile": self.profile,
            "case_id": self.case_id,
            "candidate_sha": self.candidate_sha,
            "ready_at": self.ready_at,
        }
        return {**core, "receipt_digest": _digest(core)}


@dataclass(frozen=True)
class BoundCaptureReceipt:
    plan: CapturePlanReceipt
    recording: RecordingReceipt

    def as_dict(self) -> dict[str, object]:
        recorded = self.recording.as_dict()
        core: dict[str, object] = {
            "schema": BOUND_CAPTURE_RECEIPT_SCHEMA,
            "status": "sealed",
            "capture_plan_digest": self.plan.plan_digest,
            "profile": self.recording.profile,
            "case_id": self.recording.case_id,
            "candidate_sha": self.recording.candidate_sha,
            "ready_at": self.recording.ready_at,
            "evidence_digest": self.recording.evidence_digest,
            "manifest_digest": self.recording.manifest_digest,
            "bundle_digest": self.recording.bundle_digest,
            "retention_identity": self.recording.retention_identity,
            "recording_receipt_digest": recorded["receipt_digest"],
        }
        return {**core, "receipt_digest": _digest(core)}


def prepare_capture(value: Mapping[str, Any]) -> CapturePlanReceipt:
    """Validate readiness and return the one digest all later stages bind."""

    plan = validate_capture_plan(value)
    return CapturePlanReceipt(
        _digest(plan),
        plan["profile"],
        plan["case_id"],
        plan["candidate_sha"],
        plan["ready_at"],
    )


def validate_capture_evidence(
    plan: CapturePlanReceipt,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Return canonical evidence only when it matches one prepared plan."""

    document = validate_document(evidence)
    if (
        document.get("capture_plan_digest") != plan.plan_digest
        or document["profile"] != plan.profile
        or document["case_id"] != plan.case_id
        or document["candidate_sha"] != plan.candidate_sha
        or document["ready_at"] != plan.ready_at
    ):
        raise CapturePlanError("capture evidence does not match its immutable plan")
    return document


def record_capture(
    plan_value: Mapping[str, Any],
    evidence: Mapping[str, Any],
    store_root: Path,
) -> BoundCaptureReceipt:
    """Seal evidence only when it consumes the prepared plan exactly."""

    plan = prepare_capture(plan_value)
    document = validate_capture_evidence(plan, evidence)
    return BoundCaptureReceipt(plan, record_document(document, store_root))


def verify_capture(
    plan_value: Mapping[str, Any],
    store_root: Path,
    bundle_digest: str,
) -> BoundCaptureReceipt:
    """Read back the sealed document and recompute the original plan binding."""

    plan = prepare_capture(plan_value)
    recording, document = load_verified_document(store_root, bundle_digest)
    validate_capture_evidence(plan, document)
    return BoundCaptureReceipt(plan, recording)
