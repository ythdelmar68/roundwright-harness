from __future__ import annotations

from pathlib import Path

import pytest

from roundwright_harness import capture


def digest(value: str) -> str:
    return "sha256:" + value * 64


def plan(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "roundwright-harness-capture-plan/v1",
        "profile": "roundwright-shadow-profile/worker-adapter/v1",
        "ready_at": 123,
        "case_id": "live-43-final",
        "candidate_sha": "a" * 40,
        "producer_identity": digest("1"),
        "exporter_identity": digest("2"),
        "comparator_identity": digest("3"),
        "recorder_identity": digest("4"),
        "store_identity": digest("5"),
        "observation_identity": digest("6"),
    }
    value.update(updates)
    return value


def evidence(plan_digest: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "roundwright-shadow-case/v2",
        "profile": "roundwright-shadow-profile/worker-adapter/v1",
        "ready_at": 123,
        "case_id": "live-43-final",
        "candidate_sha": "a" * 40,
        "capture_plan_digest": plan_digest,
        "worker_envelope": {"status": "accepted"},
    }
    value.update(updates)
    return value


def test_plan_prepare_record_and_verify_share_one_digest(tmp_path: Path) -> None:
    capture_plan = plan()
    prepared = capture.prepare_capture(capture_plan)

    sealed = capture.record_capture(
        capture_plan,
        evidence(prepared.plan_digest),
        tmp_path / "store",
    )
    verified = capture.verify_capture(
        capture_plan,
        tmp_path / "store",
        sealed.recording.bundle_digest,
    )

    assert sealed == verified
    assert sealed.plan == prepared
    assert sealed.as_dict()["capture_plan_digest"] == prepared.plan_digest
    assert sealed.as_dict()["recording_receipt_digest"] == sealed.recording.as_dict()["receipt_digest"]


@pytest.mark.parametrize(
    ("plan_update", "evidence_update"),
    [
        ({"profile": "roundwright-shadow-profile/other/v1"}, {}),
        ({"candidate_sha": "b" * 40}, {}),
        ({"ready_at": 124}, {}),
        ({"producer_identity": digest("7")}, {}),
        ({"exporter_identity": digest("7")}, {}),
        ({"comparator_identity": digest("7")}, {}),
        ({"recorder_identity": digest("7")}, {}),
        ({"store_identity": digest("7")}, {}),
        ({"observation_identity": digest("7")}, {}),
        ({}, {"capture_plan_digest": digest("f")}),
        ({}, {"ready_at": 124}),
        ({}, {"candidate_sha": "b" * 40}),
        ({}, {"case_id": "different-case"}),
    ],
)
def test_every_plan_or_evidence_drift_blocks_before_recording(
    tmp_path: Path,
    plan_update: dict[str, object],
    evidence_update: dict[str, object],
) -> None:
    original = plan()
    prepared = capture.prepare_capture(original)
    moved = plan(**plan_update)

    with pytest.raises(capture.CapturePlanError):
        capture.record_capture(
            moved,
            evidence(prepared.plan_digest, **evidence_update),
            tmp_path / "store",
        )

    assert not (tmp_path / "store").exists()


def test_verify_rejects_a_different_plan_after_seal(tmp_path: Path) -> None:
    original = plan()
    prepared = capture.prepare_capture(original)
    sealed = capture.record_capture(original, evidence(prepared.plan_digest), tmp_path / "store")
    moved = plan(observation_identity=digest("9"))

    with pytest.raises(capture.CapturePlanError):
        capture.verify_capture(moved, tmp_path / "store", sealed.recording.bundle_digest)


@pytest.mark.parametrize(
    "updates",
    [
        {"extra": "unknown"},
        {"ready_at": True},
        {"ready_at": -1},
        {"candidate_sha": "main"},
        {"producer_identity": "producer"},
        {"case_id": "contains spaces"},
    ],
)
def test_plan_schema_is_closed(updates: dict[str, object]) -> None:
    with pytest.raises(capture.CapturePlanError):
        capture.prepare_capture(plan(**updates))
