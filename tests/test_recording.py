from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from roundwright_harness import recording


def _case(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "roundwright-shadow-case/v2",
        "profile": "roundwright-shadow-profile/provenance-decision/v1",
        "ready_at": 123,
        "case_id": "issue-47-candidate",
        "candidate_sha": "a" * 40,
        "correlation_identity": "task-47",
        "provider_attempts": [
            {"attempt_id": "worker-1", "role": "worker"},
            {"attempt_id": "supervisor-1", "role": "supervisor"},
        ],
        "events": [
            {"event_id": "event-1", "provider_attempt_id": "worker-1"},
            {"event_id": "event-2", "provider_attempt_id": "supervisor-1"},
            {"event_id": "event-3", "provider_attempt_id": None},
        ],
    }
    value.update(updates)
    return value


def test_recording_is_content_addressed_idempotent_and_path_free(tmp_path: Path) -> None:
    store = tmp_path / "store"

    first = recording.record_document(_case(), store)
    second = recording.record_document(_case(), store)

    assert first == second
    receipt = first.as_dict()
    assert receipt["schema"] == "roundwright-harness-recording-receipt/v1"
    assert receipt["status"] == "sealed"
    assert receipt["ready_at"] == 123
    assert all(str(value).find(str(tmp_path)) == -1 for value in receipt.values())
    assert len(list(store.glob("*.bundle.json"))) == 1
    assert len(list(store.glob("*.receipt.json"))) == 1
    assert recording.verify_recording(store, first.bundle_digest) == first


def test_recording_rejects_overwrite_of_tampered_content(tmp_path: Path) -> None:
    store = tmp_path / "store"
    receipt = recording.record_document(_case(), store)
    identity = receipt.bundle_digest.removeprefix("sha256:")
    (store / f"{identity}.bundle.json").write_text("tampered", encoding="utf-8")

    with pytest.raises(recording.RecordingError):
        recording.record_document(_case(), store)
    with pytest.raises(recording.RecordingError):
        recording.verify_recording(store, receipt.bundle_digest)


def test_read_back_rejects_tampered_receipt(tmp_path: Path) -> None:
    store = tmp_path / "store"
    receipt = recording.record_document(_case(), store)
    identity = receipt.bundle_digest.removeprefix("sha256:")
    (store / f"{identity}.receipt.json").write_text("{}", encoding="utf-8")

    with pytest.raises(recording.RecordingError):
        recording.verify_recording(store, receipt.bundle_digest)


def test_read_back_rejects_floating_or_missing_identity(tmp_path: Path) -> None:
    with pytest.raises(recording.RecordingError):
        recording.verify_recording(tmp_path, "main")
    with pytest.raises(OSError):
        recording.verify_recording(tmp_path, "sha256:" + "0" * 64)


@pytest.mark.parametrize(
    "updates",
    [
        {"schema": "roundwright-shadow-case/v1"},
        {"profile": "provenance"},
        {"ready_at": True},
        {"ready_at": -1},
        {"case_id": "contains spaces"},
        {"candidate_sha": "floating-main"},
        {"raw_provider_payload": {"text": "do not retain"}},
        {"owner_reasoning": "private"},
        {"artifact": "C:\\Users\\owner\\private.json"},
        {"ratio": 0.5},
    ],
)
def test_recording_rejects_invalid_or_non_public_evidence(
    tmp_path: Path, updates: dict[str, object]
) -> None:
    with pytest.raises(recording.RecordingError):
        recording.record_document(_case(**updates), tmp_path / "store")


def test_loader_rejects_duplicate_keys_and_non_finite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
    non_finite = tmp_path / "nan.json"
    non_finite.write_text('{"value":NaN}', encoding="utf-8")

    with pytest.raises(recording.RecordingError):
        recording.load_document(duplicate)
    with pytest.raises(recording.RecordingError):
        recording.load_document(non_finite)


def test_sealed_bundle_binds_manifest_and_capture_time(tmp_path: Path) -> None:
    store = tmp_path / "store"
    receipt = recording.record_document(_case(), store)
    identity = receipt.bundle_digest.removeprefix("sha256:")
    bundle = json.loads((store / f"{identity}.bundle.json").read_text(encoding="utf-8"))
    bundle_bytes = (store / f"{identity}.bundle.json").read_bytes()

    assert bundle["schema"] == "roundwright-harness-recording-bundle/v1"
    assert bundle["manifest"]["ready_at"] == 123
    assert bundle["manifest"]["evidence_digest"] == receipt.evidence_digest
    assert bundle["manifest_digest"] == receipt.manifest_digest
    assert "sha256:" + hashlib.sha256(bundle_bytes).hexdigest() == receipt.bundle_digest
