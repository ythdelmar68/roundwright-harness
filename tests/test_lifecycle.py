from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from roundwright_harness import lifecycle


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def plan(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": lifecycle.LIFECYCLE_PLAN_SCHEMA,
        "window_identity": digest("window-49"),
        "repository_identity": digest("public-repository"),
        "candidate_sha": "a" * 40,
        "ready_at": 100,
        "producer_identity": digest("producer"),
        "store_identity": digest("store"),
        "capture_plan_digest": digest("capture-plan"),
        "review_epoch": 1,
        "review_round": 2,
        "review_mode": "complete",
    }
    value.update(updates)
    return value


def event(
    sequence: int,
    predecessor: str | None,
    *,
    attempt: int = 1,
    transition: str = "attempt_started",
    disposition: str = "pending",
    accepted_result: bool = False,
    successor_candidate_sha: str | None = None,
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": lifecycle.LIFECYCLE_EVENT_SCHEMA,
        "window_identity": digest("window-49"),
        "repository_identity": digest("public-repository"),
        "candidate_sha": "a" * 40,
        "sequence": sequence,
        "occurred_at": 100 + sequence,
        "role": "supervisor",
        "task_identity": digest(f"task-{attempt}"),
        "attempt_identity": digest(f"attempt-{attempt}"),
        "review_epoch": 1,
        "review_round": 2,
        "review_mode": "complete",
        "review_attempt": attempt,
        "transition": transition,
        "disposition": disposition,
        "accepted_result": accepted_result,
        "successor_candidate_sha": successor_candidate_sha,
        "predecessor_event_digest": predecessor,
        "artifact_references": [],
    }
    value.update(updates)
    return value


def append(
    capture_plan: dict[str, object],
    store: Path,
    values: list[dict[str, object]],
) -> list[lifecycle.LifecycleEventReceipt]:
    receipts: list[lifecycle.LifecycleEventReceipt] = []
    predecessor: str | None = None
    for sequence, updates in enumerate(values):
        item = event(sequence, predecessor, **updates)
        receipt = lifecycle.append_lifecycle_event(capture_plan, item, store)
        receipts.append(receipt)
        predecessor = receipt.event_digest
    return receipts


def test_prepare_append_seal_verify_preserves_failover_and_one_round(
    tmp_path: Path,
) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    armed = lifecycle.prepare_lifecycle(capture_plan, store)

    receipts = append(
        capture_plan,
        store,
        [
            {"attempt": 1},
            {"attempt": 1, "transition": "attempt_completed", "disposition": "cancelled"},
            {"attempt": 2},
            {"attempt": 2, "transition": "attempt_completed", "disposition": "invalid_context"},
            {"attempt": 3},
            {"attempt": 3, "transition": "attempt_completed", "disposition": "pass"},
            {
                "attempt": 3,
                "transition": "result_accepted",
                "disposition": "accepted",
                "accepted_result": True,
            },
            {
                "attempt": 3,
                "transition": "formal_round_advanced",
                "disposition": "accepted",
                "accepted_result": True,
            },
        ],
    )

    sealed = lifecycle.seal_lifecycle(capture_plan, store)
    verified, events = lifecycle.load_verified_lifecycle(store, sealed.ledger_digest)

    assert verified == sealed
    assert armed.plan_digest == sealed.plan_digest
    assert sealed.event_count == 8
    assert sealed.head_event_digest == receipts[-1].event_digest
    assert [item["disposition"] for item in events] == [
        "pending",
        "cancelled",
        "pending",
        "invalid_context",
        "pending",
        "pass",
        "accepted",
        "accepted",
    ]
    assert {(item["review_epoch"], item["review_round"], item["review_mode"]) for item in events} == {
        (1, 2, "complete")
    }
    assert str(tmp_path) not in json.dumps(sealed.as_dict(), sort_keys=True)


def test_append_requires_a_persisted_armed_plan(tmp_path: Path) -> None:
    with pytest.raises((lifecycle.LifecycleLedgerError, OSError)):
        lifecycle.append_lifecycle_event(plan(), event(0, None), tmp_path / "store")


@pytest.mark.parametrize(
    "updates",
    [
        {"candidate_sha": "b" * 40},
        {"window_identity": digest("different-window")},
        {"repository_identity": digest("different-repository")},
        {"review_epoch": 2},
        {"review_round": 3},
        {"review_mode": "converging"},
    ],
)
def test_event_identity_drift_blocks_before_append(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(capture_plan, store)

    with pytest.raises(lifecycle.LifecycleLedgerError):
        lifecycle.append_lifecycle_event(
            capture_plan,
            event(0, None, **updates),
            store,
        )

    assert list((store / "lifecycle" / digest("window-49").removeprefix("sha256:")).glob("event-*")) == []


@pytest.mark.parametrize(
    "updates",
    [
        {"sequence": 1},
        {"predecessor_event_digest": digest("invented")},
        {"raw_provider_payload": "forbidden"},
        {"occurred_at": 99},
        {"transition": "attempt_completed", "disposition": "accepted"},
        {"accepted_result": True},
        {"successor_candidate_sha": "b" * 40},
    ],
)
def test_closed_event_contract_rejects_conflicts(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(capture_plan, store)
    value = event(0, None)
    value.update(updates)

    with pytest.raises(lifecycle.LifecycleLedgerError):
        lifecycle.append_lifecycle_event(capture_plan, value, store)


def test_candidate_movement_is_explicit_and_invalidates_old_window(
    tmp_path: Path,
) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(capture_plan, store)

    receipt = lifecycle.append_lifecycle_event(
        capture_plan,
        event(
            0,
            None,
            transition="candidate_moved",
            disposition="stale",
            successor_candidate_sha="b" * 40,
        ),
        store,
    )
    sealed = lifecycle.seal_lifecycle(capture_plan, store)
    _, events = lifecycle.load_verified_lifecycle(store, sealed.ledger_digest)

    assert receipt.sequence == 0
    assert events[0]["successor_candidate_sha"] == "b" * 40


def test_sealed_window_rejects_later_append(tmp_path: Path) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(capture_plan, store)
    first = lifecycle.append_lifecycle_event(capture_plan, event(0, None), store)
    lifecycle.seal_lifecycle(capture_plan, store)

    with pytest.raises(lifecycle.LifecycleLedgerError):
        lifecycle.append_lifecycle_event(
            capture_plan,
            event(1, first.event_digest),
            store,
        )


def test_verify_detects_retained_event_tampering(tmp_path: Path) -> None:
    capture_plan = plan()
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(capture_plan, store)
    lifecycle.append_lifecycle_event(capture_plan, event(0, None), store)
    sealed = lifecycle.seal_lifecycle(capture_plan, store)
    window = store / "lifecycle" / digest("window-49").removeprefix("sha256:")
    event_path = window / "event-00000000.json"
    retained = json.loads(event_path.read_text(encoding="utf-8"))
    retained["occurred_at"] = 999
    event_path.write_text(json.dumps(retained), encoding="utf-8")

    with pytest.raises(lifecycle.LifecycleLedgerError):
        lifecycle.verify_lifecycle(store, sealed.ledger_digest)


def test_same_window_cannot_be_rearmed_with_a_changed_plan(tmp_path: Path) -> None:
    store = tmp_path / "store"
    lifecycle.prepare_lifecycle(plan(), store)

    with pytest.raises(lifecycle.LifecycleLedgerError):
        lifecycle.prepare_lifecycle(plan(producer_identity=digest("moved")), store)
