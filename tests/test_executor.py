from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from roundwright_harness import executor


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def plan(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "roundwright-harness-capture-plan/v1",
        "profile": "roundwright-shadow-profile/executor-contract-synthetic/v1",
        "ready_at": 123,
        "case_id": "synthetic-one-shot",
        "candidate_sha": "a" * 40,
        "producer_identity": digest("producer"),
        "exporter_identity": digest("exporter"),
        "comparator_identity": digest("comparator"),
        "recorder_identity": digest("recorder"),
        "store_identity": digest("store"),
        "observation_identity": digest("observation"),
    }
    value.update(updates)
    return value


def request(**plan_updates: object) -> dict[str, object]:
    return {
        "schema": executor.EXECUTOR_REQUEST_SCHEMA,
        "capture_plan": plan(**plan_updates),
    }


@dataclass
class FakeAdapter:
    components: executor.ProfileComponentIdentities = field(
        default_factory=lambda: executor.ProfileComponentIdentities(
            digest("producer"), digest("exporter"), digest("comparator")
        )
    )
    calls: list[object] = field(default_factory=list)
    fail_validation: bool = False
    malformed_projection: bool = False
    mutation_count: int = 0

    @property
    def component_identities(self) -> executor.ProfileComponentIdentities:
        return self.components

    def validate(self, binding: executor.ExecutorBinding) -> None:
        self.calls.append(("validate", binding.ready_at))
        if self.fail_validation:
            raise executor.ExecutorError("synthetic preflight failure")

    def execute(self, binding: executor.ExecutorBinding) -> executor.ProfileExecution:
        self.calls.append(("execute", binding.ready_at))
        return executor.ProfileExecution({"outcome": "accepted"}, self.mutation_count)

    def project(
        self,
        binding: executor.ExecutorBinding,
        execution: executor.ProfileExecution,
    ) -> dict[str, object]:
        self.calls.append(("project", binding.ready_at))
        if self.malformed_projection:
            return {"schema": "wrong"}
        return {
            "schema": "roundwright-shadow-case/v2",
            "profile": binding.profile,
            "ready_at": binding.ready_at,
            "case_id": binding.case_id,
            "candidate_sha": binding.candidate_sha,
            "capture_plan_digest": binding.plan.plan_digest,
            "synthetic_result": execution.value,
        }

    def compare(
        self,
        binding: executor.ExecutorBinding,
        evidence: object,
    ) -> executor.ProfileComparison:
        self.calls.append(("compare", binding.ready_at))
        return executor.ProfileComparison("pass", digest("comparison"))


def readiness(
    value: dict[str, object],
    adapter: FakeAdapter,
    store: Path,
) -> executor.ExecutorReadinessReceipt:
    receipt = executor.run_profile_executor("validate", value, adapter, store)
    assert type(receipt) is executor.ExecutorReadinessReceipt
    return receipt


def test_single_entrypoint_validates_and_executes_one_exact_binding(tmp_path: Path) -> None:
    value = request()
    dry_adapter = FakeAdapter()
    ready = readiness(value, dry_adapter, tmp_path / "store")
    assert dry_adapter.calls == [("validate", 123)]
    assert ready.as_dict()["dispatch_count"] == 0
    assert not (tmp_path / "store").exists()

    live_adapter = FakeAdapter()
    result = executor.run_profile_executor(
        "execute",
        value,
        live_adapter,
        tmp_path / "store",
        expected_readiness_digest=str(ready.as_dict()["receipt_digest"]),
    )

    assert type(result) is executor.ExecutorResultReceipt
    receipt = result.as_dict()
    assert receipt["status"] == "pass"
    assert receipt["state"] == "VERIFIED"
    assert (receipt["dispatch_count"], receipt["record_count"], receipt["verify_count"]) == (
        1,
        1,
        1,
    )
    assert receipt["mutation_count"] == 0
    assert live_adapter.calls == [
        ("validate", 123),
        ("execute", 123),
        ("project", 123),
        ("compare", 123),
    ]
    assert len(list((tmp_path / "store").glob("*.bundle.json"))) == 1
    assert len(list((tmp_path / "store").glob("*.receipt.json"))) == 1


def test_provider_free_failure_has_zero_actions_and_no_store(tmp_path: Path) -> None:
    adapter = FakeAdapter(fail_validation=True)
    runner = executor.ProfileExecutor(request(), adapter, tmp_path / "store")

    with pytest.raises(executor.ExecutorError):
        runner.validate_only()

    assert runner.state is executor.ExecutorState.STALE
    assert adapter.calls == [("validate", 123)]
    assert not (tmp_path / "store").exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"candidate_sha": "b" * 40},
        {"case_id": "moved-case"},
        {"ready_at": 124},
        {"observation_identity": digest("moved-observation")},
    ],
)
def test_stale_validate_execute_binding_blocks_before_dispatch(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    ready = readiness(request(), FakeAdapter(), tmp_path / "store")
    adapter = FakeAdapter()

    with pytest.raises(executor.ExecutorError):
        executor.run_profile_executor(
            "execute",
            request(**updates),
            adapter,
            tmp_path / "store",
            expected_readiness_digest=str(ready.as_dict()["receipt_digest"]),
        )

    assert adapter.calls == [("validate", updates.get("ready_at", 123))]
    assert not (tmp_path / "store").exists()


def test_component_mismatch_blocks_before_dispatch(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        components=executor.ProfileComponentIdentities(
            digest("other"), digest("exporter"), digest("comparator")
        )
    )

    with pytest.raises(executor.ExecutorError):
        executor.ProfileExecutor(request(), adapter, tmp_path / "store").validate_only()

    assert adapter.calls == []
    assert not (tmp_path / "store").exists()


def test_one_executor_cannot_consume_a_plan_twice(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    runner = executor.ProfileExecutor(request(), adapter, tmp_path / "store")
    ready = runner.validate_only()
    runner.execute(str(ready.as_dict()["receipt_digest"]))

    with pytest.raises(executor.ExecutorError):
        runner.execute(str(ready.as_dict()["receipt_digest"]))

    assert [call[0] for call in adapter.calls].count("execute") == 1


def test_malformed_projection_never_records_or_verifies(tmp_path: Path) -> None:
    value = request()
    ready = readiness(value, FakeAdapter(), tmp_path / "store")
    adapter = FakeAdapter(malformed_projection=True)
    runner = executor.ProfileExecutor(value, adapter, tmp_path / "store")

    with pytest.raises(ValueError):
        runner.execute(str(ready.as_dict()["receipt_digest"]))

    assert runner.state is executor.ExecutorState.STALE
    assert [call[0] for call in adapter.calls] == ["validate", "execute", "project"]
    assert not (tmp_path / "store").exists()


def test_partial_seal_never_returns_a_verified_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = request()
    ready = readiness(value, FakeAdapter(), tmp_path / "store")
    adapter = FakeAdapter()
    runner = executor.ProfileExecutor(value, adapter, tmp_path / "store")

    def partial_seal(*_args: object, **_kwargs: object) -> object:
        store = tmp_path / "store"
        store.mkdir()
        (store / "partial.bundle.json").write_text("partial", encoding="utf-8")
        raise OSError("synthetic interrupted seal")

    monkeypatch.setattr(executor, "record_capture", partial_seal)
    with pytest.raises(OSError):
        runner.execute(str(ready.as_dict()["receipt_digest"]))

    assert runner.state is executor.ExecutorState.STALE
    assert list((tmp_path / "store").glob("*.receipt.json")) == []


def test_historical_comparison_uses_bound_ready_at_not_current_time(tmp_path: Path) -> None:
    value = request(ready_at=7)
    ready = readiness(value, FakeAdapter(), tmp_path / "store")
    adapter = FakeAdapter()
    result = executor.run_profile_executor(
        "execute",
        value,
        adapter,
        tmp_path / "store",
        expected_readiness_digest=str(ready.as_dict()["receipt_digest"]),
    )

    assert type(result) is executor.ExecutorResultReceipt
    assert result.as_dict()["ready_at"] == 7
    assert adapter.calls[-1] == ("compare", 7)


def test_result_receipt_is_path_free_and_canonical(tmp_path: Path) -> None:
    value = request()
    ready = readiness(value, FakeAdapter(), tmp_path / "store")
    result = executor.run_profile_executor(
        "execute",
        value,
        FakeAdapter(),
        tmp_path / "store",
        expected_readiness_digest=str(ready.as_dict()["receipt_digest"]),
    )
    encoded = json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":"))

    assert str(tmp_path) not in encoded
    assert result.as_dict()["receipt_digest"].startswith("sha256:")
