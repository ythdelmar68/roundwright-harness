"""Phase-neutral, append-only lifecycle observation ledger.

The harness validates and seals generic public-safe transition facts.  Product
repositories own the adapter that gives those facts semantic meaning.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


LIFECYCLE_PLAN_SCHEMA = "roundwright-harness-lifecycle-plan/v1"
LIFECYCLE_PLAN_RECEIPT_SCHEMA = "roundwright-harness-lifecycle-plan-receipt/v1"
LIFECYCLE_EVENT_SCHEMA = "roundwright-harness-lifecycle-event/v1"
LIFECYCLE_EVENT_RECEIPT_SCHEMA = "roundwright-harness-lifecycle-event-receipt/v1"
LIFECYCLE_MANIFEST_SCHEMA = "roundwright-harness-lifecycle-manifest/v1"
LIFECYCLE_BUNDLE_SCHEMA = "roundwright-harness-lifecycle-bundle/v1"
LIFECYCLE_SEAL_RECEIPT_SCHEMA = "roundwright-harness-lifecycle-seal-receipt/v1"
LIFECYCLE_RETENTION_SCHEMA = "roundwright-harness-lifecycle-retention/v1"
LIFECYCLE_STATUS_SCHEMA = "roundwright-harness-lifecycle-status/v1"

_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PLAN_FIELDS = {
    "schema",
    "window_identity",
    "repository_identity",
    "candidate_sha",
    "ready_at",
    "producer_identity",
    "store_identity",
    "capture_plan_digest",
    "review_epoch",
    "review_round",
    "review_mode",
}
_EVENT_FIELDS = {
    "schema",
    "window_identity",
    "repository_identity",
    "candidate_sha",
    "sequence",
    "occurred_at",
    "role",
    "task_identity",
    "attempt_identity",
    "review_epoch",
    "review_round",
    "review_mode",
    "review_attempt",
    "transition",
    "disposition",
    "accepted_result",
    "successor_candidate_sha",
    "predecessor_event_digest",
    "artifact_references",
}
_ROLES = {"worker", "supervisor"}
_REVIEW_MODES = {"complete", "converging"}
_TRANSITIONS = {
    "attempt_started",
    "attempt_completed",
    "result_accepted",
    "result_unaccepted",
    "candidate_moved",
    "formal_round_advanced",
}
_DISPOSITIONS = {
    "pending",
    "cancelled",
    "invalid_context",
    "pass",
    "findings",
    "failed",
    "accepted",
    "unaccepted",
    "stale",
}
_TRANSITION_DISPOSITIONS = {
    "attempt_started": {"pending"},
    "attempt_completed": {
        "cancelled",
        "invalid_context",
        "pass",
        "findings",
        "failed",
    },
    "result_accepted": {"accepted"},
    "result_unaccepted": {"unaccepted"},
    "candidate_moved": {"stale"},
    "formal_round_advanced": {"accepted"},
}


class LifecycleLedgerError(ValueError):
    """The lifecycle ledger binding or retained data is invalid."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _reject_constant(_value: str) -> None:
    raise LifecycleLedgerError("non-finite lifecycle JSON number")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise LifecycleLedgerError("duplicate lifecycle JSON key")
        value[key] = item
    return value


def _load_bytes(value: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleLedgerError("invalid lifecycle JSON") from error
    if type(decoded) is not dict:
        raise LifecycleLedgerError("lifecycle document must be an object")
    return decoded


def _write_once(path: Path, value: bytes) -> None:
    if path.is_symlink():
        raise LifecycleLedgerError("lifecycle target must not be a symlink")
    try:
        with path.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != value:
            raise LifecycleLedgerError("append-only lifecycle conflict") from None


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise LifecycleLedgerError(f"invalid {label} identity")
    return value


def _require_positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise LifecycleLedgerError(f"invalid {label}")
    return value


def validate_lifecycle_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one canonical closed plan that can be armed before event one."""

    if type(value) is not dict or set(value) != _PLAN_FIELDS:
        raise LifecycleLedgerError("lifecycle plan is incomplete")
    if value["schema"] != LIFECYCLE_PLAN_SCHEMA:
        raise LifecycleLedgerError("lifecycle plan schema is unsupported")
    for field in (
        "window_identity",
        "repository_identity",
        "producer_identity",
        "store_identity",
        "capture_plan_digest",
    ):
        _require_digest(value[field], field)
    if type(value["candidate_sha"]) is not str or _SHA.fullmatch(value["candidate_sha"]) is None:
        raise LifecycleLedgerError("invalid lifecycle candidate")
    if type(value["ready_at"]) is not int or value["ready_at"] < 0:
        raise LifecycleLedgerError("invalid lifecycle evidence time")
    _require_positive_int(value["review_epoch"], "review epoch")
    _require_positive_int(value["review_round"], "review round")
    if value["review_mode"] not in _REVIEW_MODES:
        raise LifecycleLedgerError("invalid review mode")
    return json.loads(_canonical_bytes(value))


@dataclass(frozen=True)
class LifecyclePlanReceipt:
    plan_digest: str
    window_identity: str
    repository_identity: str
    candidate_sha: str
    ready_at: int
    review_epoch: int
    review_round: int
    review_mode: str

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": LIFECYCLE_PLAN_RECEIPT_SCHEMA,
            "status": "armed",
            "event_schema": LIFECYCLE_EVENT_SCHEMA,
            "plan_digest": self.plan_digest,
            "window_identity": self.window_identity,
            "repository_identity": self.repository_identity,
            "candidate_sha": self.candidate_sha,
            "ready_at": self.ready_at,
            "review_epoch": self.review_epoch,
            "review_round": self.review_round,
            "review_mode": self.review_mode,
        }
        return {**core, "receipt_digest": _digest(core)}


@dataclass(frozen=True)
class LifecycleEventReceipt:
    plan_digest: str
    window_identity: str
    sequence: int
    event_digest: str
    predecessor_event_digest: str | None
    previous_entry_digest: str | None
    entry_digest: str

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": LIFECYCLE_EVENT_RECEIPT_SCHEMA,
            "status": "appended",
            "plan_digest": self.plan_digest,
            "window_identity": self.window_identity,
            "sequence": self.sequence,
            "event_digest": self.event_digest,
            "predecessor_event_digest": self.predecessor_event_digest,
            "previous_entry_digest": self.previous_entry_digest,
            "entry_digest": self.entry_digest,
        }
        return {**core, "receipt_digest": _digest(core)}


@dataclass(frozen=True)
class LifecycleSealReceipt:
    plan_digest: str
    window_identity: str
    repository_identity: str
    candidate_sha: str
    ready_at: int
    event_count: int
    head_event_digest: str
    head_entry_digest: str
    manifest_digest: str
    ledger_digest: str
    retention_identity: str

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": LIFECYCLE_SEAL_RECEIPT_SCHEMA,
            "status": "sealed",
            "event_schema": LIFECYCLE_EVENT_SCHEMA,
            "plan_digest": self.plan_digest,
            "window_identity": self.window_identity,
            "repository_identity": self.repository_identity,
            "candidate_sha": self.candidate_sha,
            "ready_at": self.ready_at,
            "event_count": self.event_count,
            "head_event_digest": self.head_event_digest,
            "head_entry_digest": self.head_entry_digest,
            "manifest_digest": self.manifest_digest,
            "ledger_digest": self.ledger_digest,
            "retention_identity": self.retention_identity,
        }
        return {**core, "receipt_digest": _digest(core)}


def _plan_receipt(plan: Mapping[str, Any]) -> LifecyclePlanReceipt:
    return LifecyclePlanReceipt(
        plan_digest=_digest(plan),
        window_identity=plan["window_identity"],
        repository_identity=plan["repository_identity"],
        candidate_sha=plan["candidate_sha"],
        ready_at=plan["ready_at"],
        review_epoch=plan["review_epoch"],
        review_round=plan["review_round"],
        review_mode=plan["review_mode"],
    )


def _window_root(store_root: Path, window_identity: str) -> Path:
    return store_root / "lifecycle" / window_identity.removeprefix("sha256:")


def prepare_lifecycle(
    plan_value: Mapping[str, Any],
    store_root: Path,
) -> LifecyclePlanReceipt:
    """Arm one exact window and persist its canonical plan before event one."""

    plan = validate_lifecycle_plan(plan_value)
    receipt = _plan_receipt(plan)
    store_root.mkdir(parents=True, exist_ok=True)
    if store_root.is_symlink():
        raise LifecycleLedgerError("lifecycle store must not be a symlink")
    root = _window_root(store_root, receipt.window_identity)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise LifecycleLedgerError("lifecycle window must not be a symlink")
    _write_once(root / "plan.json", _canonical_bytes(plan) + b"\n")
    _write_once(root / "armed.receipt.json", _canonical_bytes(receipt.as_dict()) + b"\n")
    return _load_prepared(plan, store_root)[1]


def _load_prepared(
    plan_value: Mapping[str, Any],
    store_root: Path,
) -> tuple[dict[str, Any], LifecyclePlanReceipt]:
    plan = validate_lifecycle_plan(plan_value)
    expected = _plan_receipt(plan)
    root = _window_root(store_root, expected.window_identity)
    if store_root.is_symlink() or root.is_symlink():
        raise LifecycleLedgerError("lifecycle store must not be a symlink")
    plan_path = root / "plan.json"
    receipt_path = root / "armed.receipt.json"
    if plan_path.is_symlink() or receipt_path.is_symlink():
        raise LifecycleLedgerError("lifecycle target must not be a symlink")
    retained_plan = _load_bytes(plan_path.read_bytes())
    retained_receipt = _load_bytes(receipt_path.read_bytes())
    if retained_plan != plan or plan_path.read_bytes() != _canonical_bytes(plan) + b"\n":
        raise LifecycleLedgerError("armed lifecycle plan mismatch")
    if retained_receipt != expected.as_dict() or receipt_path.read_bytes() != _canonical_bytes(retained_receipt) + b"\n":
        raise LifecycleLedgerError("armed lifecycle receipt mismatch")
    return plan, expected


def validate_lifecycle_event(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
    *,
    expected_sequence: int,
    expected_predecessor: str | None,
) -> dict[str, Any]:
    """Validate one closed generic event against its immutable window."""

    if type(value) is not dict or set(value) != _EVENT_FIELDS:
        raise LifecycleLedgerError("lifecycle event is incomplete")
    if value["schema"] != LIFECYCLE_EVENT_SCHEMA:
        raise LifecycleLedgerError("lifecycle event schema is unsupported")
    for field in ("window_identity", "repository_identity", "task_identity", "attempt_identity"):
        _require_digest(value[field], field)
    if type(value["candidate_sha"]) is not str or _SHA.fullmatch(value["candidate_sha"]) is None:
        raise LifecycleLedgerError("invalid event candidate")
    if type(value["sequence"]) is not int or value["sequence"] != expected_sequence:
        raise LifecycleLedgerError("lifecycle sequence is not append-only")
    if type(value["occurred_at"]) is not int or value["occurred_at"] < plan["ready_at"]:
        raise LifecycleLedgerError("event predates its armed window")
    if value["role"] not in _ROLES:
        raise LifecycleLedgerError("invalid lifecycle role")
    if value["review_mode"] not in _REVIEW_MODES:
        raise LifecycleLedgerError("invalid event review mode")
    for field in ("review_epoch", "review_round", "review_attempt"):
        _require_positive_int(value[field], field.replace("_", " "))
    if value["transition"] not in _TRANSITIONS or value["disposition"] not in _DISPOSITIONS:
        raise LifecycleLedgerError("invalid lifecycle transition")
    if value["disposition"] not in _TRANSITION_DISPOSITIONS[value["transition"]]:
        raise LifecycleLedgerError("transition and disposition conflict")
    if type(value["accepted_result"]) is not bool:
        raise LifecycleLedgerError("accepted-result flag is invalid")
    accepted_transition = value["transition"] in {"result_accepted", "formal_round_advanced"}
    if value["accepted_result"] is not accepted_transition:
        raise LifecycleLedgerError("accepted-result flag conflicts with transition")
    successor = value["successor_candidate_sha"]
    if value["transition"] == "candidate_moved":
        if type(successor) is not str or _SHA.fullmatch(successor) is None or successor == value["candidate_sha"]:
            raise LifecycleLedgerError("candidate movement is invalid")
    elif successor is not None:
        raise LifecycleLedgerError("unexpected successor candidate")
    predecessor = value["predecessor_event_digest"]
    if predecessor != expected_predecessor:
        raise LifecycleLedgerError("event predecessor mismatch")
    if predecessor is not None:
        _require_digest(predecessor, "predecessor event")
    references = value["artifact_references"]
    if type(references) is not list or len(references) > 16:
        raise LifecycleLedgerError("artifact reference list is invalid")
    if any(type(item) is not str or _DIGEST.fullmatch(item) is None for item in references):
        raise LifecycleLedgerError("artifact reference is invalid")
    for field in ("window_identity", "repository_identity", "candidate_sha", "review_epoch", "review_round", "review_mode"):
        if value[field] != plan[field]:
            raise LifecycleLedgerError("event moved outside its armed binding")
    return json.loads(_canonical_bytes(value))


def _event_paths(root: Path, sequence: int) -> tuple[Path, Path]:
    prefix = f"event-{sequence:08d}"
    return root / f"{prefix}.json", root / f"{prefix}.receipt.json"


def _event_receipt(
    plan_digest: str,
    event: Mapping[str, Any],
    previous_entry_digest: str | None,
) -> LifecycleEventReceipt:
    event_digest = _digest(event)
    entry_core = {
        "schema": "roundwright-harness-lifecycle-entry/v1",
        "plan_digest": plan_digest,
        "sequence": event["sequence"],
        "event_digest": event_digest,
        "predecessor_event_digest": event["predecessor_event_digest"],
        "previous_entry_digest": previous_entry_digest,
    }
    return LifecycleEventReceipt(
        plan_digest=plan_digest,
        window_identity=event["window_identity"],
        sequence=event["sequence"],
        event_digest=event_digest,
        predecessor_event_digest=event["predecessor_event_digest"],
        previous_entry_digest=previous_entry_digest,
        entry_digest=_digest(entry_core),
    )


def _load_event_chain(
    plan: Mapping[str, Any],
    store_root: Path,
) -> tuple[list[dict[str, Any]], list[LifecycleEventReceipt]]:
    root = _window_root(store_root, plan["window_identity"])
    receipt_paths = sorted(root.glob("event-????????.receipt.json"))
    event_paths = sorted(root.glob("event-????????.json"))
    if len(receipt_paths) != len(event_paths):
        raise LifecycleLedgerError("partial lifecycle append")
    events: list[dict[str, Any]] = []
    receipts: list[LifecycleEventReceipt] = []
    predecessor_event: str | None = None
    previous_entry: str | None = None
    plan_digest = _digest(plan)
    for sequence, (event_path, receipt_path) in enumerate(zip(event_paths, receipt_paths, strict=True)):
        if event_path.is_symlink() or receipt_path.is_symlink():
            raise LifecycleLedgerError("lifecycle target must not be a symlink")
        event_bytes = event_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        event = validate_lifecycle_event(
            plan,
            _load_bytes(event_bytes),
            expected_sequence=sequence,
            expected_predecessor=predecessor_event,
        )
        receipt = _event_receipt(plan_digest, event, previous_entry)
        if event_bytes != _canonical_bytes(event) + b"\n":
            raise LifecycleLedgerError("lifecycle event is not canonical")
        if _load_bytes(receipt_bytes) != receipt.as_dict() or receipt_bytes != _canonical_bytes(receipt.as_dict()) + b"\n":
            raise LifecycleLedgerError("lifecycle event receipt mismatch")
        events.append(event)
        receipts.append(receipt)
        predecessor_event = receipt.event_digest
        previous_entry = receipt.entry_digest
    return events, receipts


def append_lifecycle_event(
    plan_value: Mapping[str, Any],
    event_value: Mapping[str, Any],
    store_root: Path,
) -> LifecycleEventReceipt:
    """Append and immediately read back one transition in the armed window."""

    plan, prepared = _load_prepared(plan_value, store_root)
    root = _window_root(store_root, prepared.window_identity)
    if (root / "sealed.receipt.json").exists():
        raise LifecycleLedgerError("sealed lifecycle window is immutable")
    events, receipts = _load_event_chain(plan, store_root)
    predecessor_event = receipts[-1].event_digest if receipts else None
    previous_entry = receipts[-1].entry_digest if receipts else None
    event = validate_lifecycle_event(
        plan,
        event_value,
        expected_sequence=len(events),
        expected_predecessor=predecessor_event,
    )
    receipt = _event_receipt(prepared.plan_digest, event, previous_entry)
    event_path, receipt_path = _event_paths(root, receipt.sequence)
    _write_once(event_path, _canonical_bytes(event) + b"\n")
    _write_once(receipt_path, _canonical_bytes(receipt.as_dict()) + b"\n")
    loaded_events, loaded_receipts = _load_event_chain(plan, store_root)
    if len(loaded_events) != len(events) + 1 or loaded_receipts[-1] != receipt:
        raise LifecycleLedgerError("lifecycle append read-back mismatch")
    return receipt


def _assemble_bundle(
    plan: Mapping[str, Any],
    events: list[dict[str, Any]],
    receipts: list[LifecycleEventReceipt],
) -> tuple[bytes, LifecycleSealReceipt]:
    if not events or len(events) != len(receipts):
        raise LifecycleLedgerError("lifecycle window has no complete events")
    plan_digest = _digest(plan)
    manifest = {
        "schema": LIFECYCLE_MANIFEST_SCHEMA,
        "event_schema": LIFECYCLE_EVENT_SCHEMA,
        "plan_digest": plan_digest,
        "window_identity": plan["window_identity"],
        "repository_identity": plan["repository_identity"],
        "candidate_sha": plan["candidate_sha"],
        "ready_at": plan["ready_at"],
        "event_count": len(events),
        "event_digests": [receipt.event_digest for receipt in receipts],
        "entry_digests": [receipt.entry_digest for receipt in receipts],
        "head_event_digest": receipts[-1].event_digest,
        "head_entry_digest": receipts[-1].entry_digest,
    }
    manifest_digest = _digest(manifest)
    bundle = {
        "schema": LIFECYCLE_BUNDLE_SCHEMA,
        "plan": plan,
        "plan_digest": plan_digest,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "events": events,
        "event_receipts": [receipt.as_dict() for receipt in receipts],
    }
    bundle_bytes = _canonical_bytes(bundle)
    ledger_digest = _digest_bytes(bundle_bytes)
    retention_identity = _digest(
        {
            "schema": LIFECYCLE_RETENTION_SCHEMA,
            "ledger_digest": ledger_digest,
            "window_identity": plan["window_identity"],
            "candidate_sha": plan["candidate_sha"],
        }
    )
    receipt = LifecycleSealReceipt(
        plan_digest=plan_digest,
        window_identity=plan["window_identity"],
        repository_identity=plan["repository_identity"],
        candidate_sha=plan["candidate_sha"],
        ready_at=plan["ready_at"],
        event_count=len(events),
        head_event_digest=receipts[-1].event_digest,
        head_entry_digest=receipts[-1].entry_digest,
        manifest_digest=manifest_digest,
        ledger_digest=ledger_digest,
        retention_identity=retention_identity,
    )
    return bundle_bytes, receipt


def seal_lifecycle(
    plan_value: Mapping[str, Any],
    store_root: Path,
) -> LifecycleSealReceipt:
    """Seal the complete chain once and retain a content-addressed bundle."""

    plan, prepared = _load_prepared(plan_value, store_root)
    events, receipts = _load_event_chain(plan, store_root)
    bundle_bytes, receipt = _assemble_bundle(plan, events, receipts)
    identity = receipt.ledger_digest.removeprefix("sha256:")
    _write_once(store_root / f"{identity}.lifecycle.bundle.json", bundle_bytes)
    _write_once(
        store_root / f"{identity}.lifecycle.receipt.json",
        _canonical_bytes(receipt.as_dict()) + b"\n",
    )
    root = _window_root(store_root, prepared.window_identity)
    _write_once(root / "sealed.receipt.json", _canonical_bytes(receipt.as_dict()) + b"\n")
    verified, _ = load_verified_lifecycle(store_root, receipt.ledger_digest)
    return verified


def load_verified_lifecycle(
    store_root: Path,
    ledger_digest: str,
) -> tuple[LifecycleSealReceipt, list[dict[str, Any]]]:
    """Read back a sealed lifecycle bundle and recompute every binding."""

    _require_digest(ledger_digest, "ledger")
    if store_root.is_symlink():
        raise LifecycleLedgerError("lifecycle store must not be a symlink")
    identity = ledger_digest.removeprefix("sha256:")
    bundle_path = store_root / f"{identity}.lifecycle.bundle.json"
    receipt_path = store_root / f"{identity}.lifecycle.receipt.json"
    if bundle_path.is_symlink() or receipt_path.is_symlink():
        raise LifecycleLedgerError("lifecycle target must not be a symlink")
    bundle_bytes = bundle_path.read_bytes()
    receipt_bytes = receipt_path.read_bytes()
    if _digest_bytes(bundle_bytes) != ledger_digest:
        raise LifecycleLedgerError("lifecycle bundle digest mismatch")
    bundle = _load_bytes(bundle_bytes)
    if set(bundle) != {"schema", "plan", "plan_digest", "manifest", "manifest_digest", "events", "event_receipts"}:
        raise LifecycleLedgerError("lifecycle bundle is incomplete")
    if bundle["schema"] != LIFECYCLE_BUNDLE_SCHEMA or type(bundle["events"]) is not list:
        raise LifecycleLedgerError("lifecycle bundle schema is unsupported")
    plan = validate_lifecycle_plan(bundle["plan"])
    _load_prepared(plan, store_root)
    retained_events, retained_receipts = _load_event_chain(plan, store_root)
    expected_bytes, receipt = _assemble_bundle(plan, retained_events, retained_receipts)
    if expected_bytes != bundle_bytes:
        raise LifecycleLedgerError("lifecycle bundle content mismatch")
    receipt_value = _load_bytes(receipt_bytes)
    if receipt_value != receipt.as_dict() or receipt_bytes != _canonical_bytes(receipt_value) + b"\n":
        raise LifecycleLedgerError("lifecycle seal receipt mismatch")
    marker = _window_root(store_root, plan["window_identity"]) / "sealed.receipt.json"
    if marker.is_symlink() or marker.read_bytes() != _canonical_bytes(receipt.as_dict()) + b"\n":
        raise LifecycleLedgerError("lifecycle seal marker mismatch")
    return receipt, retained_events


def verify_lifecycle(store_root: Path, ledger_digest: str) -> LifecycleSealReceipt:
    """Return the path-free receipt for one fully verified lifecycle ledger."""

    return load_verified_lifecycle(store_root, ledger_digest)[0]
