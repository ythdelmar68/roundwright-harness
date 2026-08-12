"""Content-addressed, public-safe Shadow evidence recording.

Roundwright owns the semantic case schema and comparison result.  The harness
owns only the reusable evidence boundary: reject unsafe material, bind the
capture identities, seal canonical bytes once, and return a path-free receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SHADOW_CASE_SCHEMA = "roundwright-shadow-case/v2"
MANIFEST_SCHEMA = "roundwright-harness-recording-manifest/v1"
BUNDLE_SCHEMA = "roundwright-harness-recording-bundle/v1"
RECEIPT_SCHEMA = "roundwright-harness-recording-receipt/v1"
STATUS_SCHEMA = "roundwright-harness-recording-status/v1"
RETENTION_SCHEMA = "roundwright-harness-retention/v1"

_PROFILE = re.compile(r"roundwright-shadow-profile/[a-z0-9-]+/v[1-9][0-9]*")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{40}")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\\\/]")
_POSIX_PRIVATE_PATH = re.compile(r"^/(?:Users|home|private|tmp|var|etc)(?:/|$)")

_FORBIDDEN_KEYS = {
    "authorization",
    "api_key",
    "chain_of_thought",
    "completion_text",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "exception_text",
    "hidden_reasoning",
    "github_payload",
    "headers",
    "local_path",
    "owner_reasoning",
    "password",
    "prompt",
    "prompts",
    "private_path",
    "provider_prose",
    "provider_input",
    "provider_output",
    "raw",
    "raw_log",
    "raw_logs",
    "raw_payload",
    "raw_provider_payload",
    "raw_provider_prose",
    "secret",
    "secrets",
    "response",
    "responses",
    "token",
    "tokens",
    "transcript",
}


class RecordingError(ValueError):
    """The proposed recording is invalid or unsafe to retain."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _reject_constant(_value: str) -> None:
    raise RecordingError("non-finite JSON number")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RecordingError("duplicate JSON key")
        value[key] = item
    return value


def load_document(path: Path) -> dict[str, Any]:
    """Load one strict JSON object without exposing its path in errors."""

    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(
                stream,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RecordingError("invalid JSON document") from error
    if type(value) is not dict:
        raise RecordingError("recording input must be an object")
    return value


def _unsafe_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized in _FORBIDDEN_KEYS
        or normalized.startswith("raw_")
        or normalized.endswith("_prose")
        or normalized.endswith("_reasoning")
        or normalized.endswith("_transcript")
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
    )


def _private_path(value: str) -> bool:
    return bool(
        _WINDOWS_ABSOLUTE_PATH.match(value)
        or _POSIX_PRIVATE_PATH.match(value)
        or value.startswith("\\\\")
    )


def _validate_public_value(value: object) -> None:
    if value is None or type(value) in (bool, int):
        return
    if type(value) is str:
        if _private_path(value):
            raise RecordingError("private path is not public-safe evidence")
        return
    if type(value) is list:
        for item in value:
            _validate_public_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 128:
                raise RecordingError("invalid evidence key")
            if _unsafe_key(key):
                raise RecordingError("forbidden evidence field")
            _validate_public_value(item)
        return
    raise RecordingError("unsupported JSON value")


def validate_document(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the generic public-safe envelope and return canonical data."""

    if type(value) is not dict:
        raise RecordingError("recording input must be an object")
    _validate_public_value(value)
    required = {"schema", "profile", "ready_at", "case_id", "candidate_sha"}
    if not required.issubset(value):
        raise RecordingError("missing recording identity")
    if value["schema"] != SHADOW_CASE_SCHEMA:
        raise RecordingError("unsupported Shadow case schema")
    if type(value["profile"]) is not str or _PROFILE.fullmatch(value["profile"]) is None:
        raise RecordingError("invalid evidence profile")
    if type(value["ready_at"]) is not int or value["ready_at"] < 0:
        raise RecordingError("invalid capture time")
    if type(value["case_id"]) is not str or _IDENTIFIER.fullmatch(value["case_id"]) is None:
        raise RecordingError("invalid case identity")
    if type(value["candidate_sha"]) is not str or _SHA.fullmatch(value["candidate_sha"]) is None:
        raise RecordingError("invalid candidate identity")
    return json.loads(_canonical_bytes(value))


@dataclass(frozen=True)
class RecordingReceipt:
    profile: str
    case_id: str
    candidate_sha: str
    ready_at: int
    evidence_digest: str
    manifest_digest: str
    bundle_digest: str
    retention_identity: str

    def as_dict(self) -> dict[str, object]:
        core: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "status": "sealed",
            "evidence_schema": SHADOW_CASE_SCHEMA,
            "profile": self.profile,
            "case_id": self.case_id,
            "candidate_sha": self.candidate_sha,
            "ready_at": self.ready_at,
            "evidence_digest": self.evidence_digest,
            "manifest_digest": self.manifest_digest,
            "bundle_digest": self.bundle_digest,
            "retention_identity": self.retention_identity,
        }
        return {**core, "receipt_digest": _digest(core)}


def _write_once(path: Path, value: bytes) -> None:
    if path.is_symlink():
        raise RecordingError("recording target must not be a symlink")
    try:
        with path.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != value:
            raise RecordingError("content-addressed recording conflict") from None


def record_document(value: Mapping[str, Any], store_root: Path) -> RecordingReceipt:
    """Seal one validated document without overwriting an existing artifact."""

    evidence = validate_document(value)
    evidence_digest = _digest(evidence)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "evidence_schema": SHADOW_CASE_SCHEMA,
        "profile": evidence["profile"],
        "case_id": evidence["case_id"],
        "candidate_sha": evidence["candidate_sha"],
        "ready_at": evidence["ready_at"],
        "evidence_digest": evidence_digest,
    }
    manifest_digest = _digest(manifest)
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "evidence": evidence,
    }
    bundle_bytes = _canonical_bytes(bundle)
    bundle_digest = _digest_bytes(bundle_bytes)
    retention_identity = _digest(
        {
            "schema": RETENTION_SCHEMA,
            "bundle_digest": bundle_digest,
            "profile": evidence["profile"],
            "case_id": evidence["case_id"],
            "candidate_sha": evidence["candidate_sha"],
        }
    )
    receipt = RecordingReceipt(
        profile=evidence["profile"],
        case_id=evidence["case_id"],
        candidate_sha=evidence["candidate_sha"],
        ready_at=evidence["ready_at"],
        evidence_digest=evidence_digest,
        manifest_digest=manifest_digest,
        bundle_digest=bundle_digest,
        retention_identity=retention_identity,
    )

    store_root.mkdir(parents=True, exist_ok=True)
    if store_root.is_symlink():
        raise RecordingError("recording store must not be a symlink")
    identity = bundle_digest.removeprefix("sha256:")
    _write_once(store_root / f"{identity}.bundle.json", bundle_bytes)
    _write_once(
        store_root / f"{identity}.receipt.json",
        _canonical_bytes(receipt.as_dict()) + b"\n",
    )
    return receipt
