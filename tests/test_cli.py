from __future__ import annotations

import json
import hashlib
from pathlib import Path

from roundwright_harness import cli
from roundwright_harness.capture import prepare_capture
from roundwright_harness import executor


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class _CliAdapter:
    @property
    def component_identities(self) -> executor.ProfileComponentIdentities:
        return executor.ProfileComponentIdentities(
            _digest("producer"), _digest("exporter"), _digest("comparator")
        )

    def validate(self, _binding: executor.ExecutorBinding) -> None:
        return None

    def execute(self, _binding: executor.ExecutorBinding) -> executor.ProfileExecution:
        return executor.ProfileExecution({"outcome": "accepted"})

    def project(
        self,
        binding: executor.ExecutorBinding,
        execution: executor.ProfileExecution,
    ) -> dict[str, object]:
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
        _binding: executor.ExecutorBinding,
        _evidence: object,
    ) -> executor.ProfileComparison:
        return executor.ProfileComparison("pass", _digest("comparison"))


def test_doctor_emits_one_public_safe_record(capsys) -> None:
    assert cli.doctor(require_roundwright=False) == 0
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema"] == "roundwright-harness/v1"
    assert record["gate"] == "doctor"
    assert record["status"] == "pass"
    assert record["python"] == "3.12"
    assert record["codex_sdk"]
    assert record["codex_runtime"]


def test_record_shadow_emits_one_public_safe_receipt(
    tmp_path: Path, capsys
) -> None:
    input_path = tmp_path / "case.json"
    input_path.write_text(
        json.dumps(
            {
                "schema": "roundwright-shadow-case/v2",
                "profile": "roundwright-shadow-profile/provenance-decision/v1",
                "ready_at": 123,
                "case_id": "case-47",
                "candidate_sha": "a" * 40,
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "record-shadow",
                "--input",
                str(input_path),
                "--store",
                str(tmp_path / "store"),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert len(lines) == 1
    receipt = json.loads(lines[0])
    assert receipt["schema"] == "roundwright-harness-recording-receipt/v1"
    assert receipt["profile"] == "roundwright-shadow-profile/provenance-decision/v1"
    assert str(tmp_path) not in output.out

    assert (
        cli.main(
            [
                "verify-shadow",
                "--store",
                str(tmp_path / "store"),
                "--bundle-digest",
                receipt["bundle_digest"],
            ]
        )
        == 0
    )
    verified = capsys.readouterr()
    assert json.loads(verified.out) == receipt
    assert str(tmp_path) not in verified.out


def test_record_shadow_blocks_without_leaking_error_or_path(
    tmp_path: Path, capsys
) -> None:
    input_path = tmp_path / "private-case.json"
    input_path.write_text(
        json.dumps(
            {
                "schema": "roundwright-shadow-case/v2",
                "profile": "roundwright-shadow-profile/provenance-decision/v1",
                "ready_at": 123,
                "case_id": "case-47",
                "candidate_sha": "a" * 40,
                "raw_provider_payload": "sensitive",
            }
        ),
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "record-shadow",
                "--input",
                str(input_path),
                "--store",
                str(tmp_path / "store"),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    record = json.loads(output.out)
    assert record == {
        "gate": "shadow-recorder",
        "reason": "invalid-or-unsafe-evidence",
        "schema": "roundwright-harness-recording-status/v1",
        "status": "blocked",
    }
    assert str(tmp_path) not in output.out
    assert "sensitive" not in output.out


def test_capture_cli_prepares_records_and_verifies_one_plan(
    tmp_path: Path, capsys
) -> None:
    plan = {
        "schema": "roundwright-harness-capture-plan/v1",
        "profile": "roundwright-shadow-profile/worker-adapter/v1",
        "ready_at": 123,
        "case_id": "live-43-final",
        "candidate_sha": "a" * 40,
        "producer_identity": "sha256:" + "1" * 64,
        "exporter_identity": "sha256:" + "2" * 64,
        "comparator_identity": "sha256:" + "3" * 64,
        "recorder_identity": "sha256:" + "4" * 64,
        "store_identity": "sha256:" + "5" * 64,
        "observation_identity": "sha256:" + "6" * 64,
    }
    plan_path = tmp_path / "plan.json"
    case_path = tmp_path / "case.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    case_path.write_text(
        json.dumps(
            {
                "schema": "roundwright-shadow-case/v2",
                "profile": plan["profile"],
                "ready_at": plan["ready_at"],
                "case_id": plan["case_id"],
                "candidate_sha": plan["candidate_sha"],
                "capture_plan_digest": prepare_capture(plan).plan_digest,
                "worker_envelope": {"status": "accepted"},
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["prepare-capture", "--plan", str(plan_path)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["status"] == "ready"
    assert str(tmp_path) not in json.dumps(prepared)

    assert cli.main(["record-capture", "--plan", str(plan_path), "--input", str(case_path), "--store", str(tmp_path / "store")]) == 0
    sealed = json.loads(capsys.readouterr().out)
    assert sealed["capture_plan_digest"] == prepared["plan_digest"]
    assert str(tmp_path) not in json.dumps(sealed)

    assert cli.main(["verify-capture", "--plan", str(plan_path), "--store", str(tmp_path / "store"), "--bundle-digest", sealed["bundle_digest"]]) == 0
    assert json.loads(capsys.readouterr().out) == sealed


def test_capture_cli_blocks_drift_without_path_or_detail(
    tmp_path: Path, capsys
) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")

    assert cli.main(["prepare-capture", "--plan", str(plan_path)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "gate": "shadow-capture-prepare",
        "reason": "invalid-or-conflicting-capture-binding",
        "schema": "roundwright-harness-capture-status/v1",
        "status": "blocked",
    }
    assert str(tmp_path) not in output


def test_profile_cli_uses_one_command_for_validate_and_execute(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    capture_plan = {
        "schema": "roundwright-harness-capture-plan/v1",
        "profile": "roundwright-shadow-profile/executor-contract-synthetic/v1",
        "ready_at": 9,
        "case_id": "cli-one-shot",
        "candidate_sha": "a" * 40,
        "producer_identity": _digest("producer"),
        "exporter_identity": _digest("exporter"),
        "comparator_identity": _digest("comparator"),
        "recorder_identity": _digest("recorder"),
        "store_identity": _digest("store"),
        "observation_identity": _digest("observation"),
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": executor.EXECUTOR_REQUEST_SCHEMA,
                "capture_plan": capture_plan,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_load_profile_adapter", lambda *_args: _CliAdapter())
    common = [
        "run-profile",
        "--request",
        str(request_path),
        "--store",
        str(tmp_path / "store"),
        "--adapter-factory",
        "public.module:factory",
    ]

    assert cli.main([*common, "--mode", "validate"]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["state"] == "PREFLIGHT_READY"
    assert ready["dispatch_count"] == 0

    assert cli.main(
        [
            *common,
            "--mode",
            "execute",
            "--expected-readiness-digest",
            ready["receipt_digest"],
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "VERIFIED"
    assert result["ready_at"] == 9
    assert (result["dispatch_count"], result["record_count"], result["verify_count"]) == (
        1,
        1,
        1,
    )
    assert str(tmp_path) not in json.dumps(result)


def test_profile_cli_provider_free_failure_reports_zero_counts(
    tmp_path: Path,
    capsys,
) -> None:
    request_path = tmp_path / "bad.json"
    request_path.write_text("{}", encoding="utf-8")

    assert cli.main(
        [
            "run-profile",
            "--mode",
            "validate",
            "--request",
            str(request_path),
            "--store",
            str(tmp_path / "store"),
            "--adapter-factory",
            "public.module:factory",
        ]
    ) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked == {
        "schema": "roundwright-harness-profile-executor-status/v1",
        "gate": "profile-executor",
        "status": "blocked",
        "state": "UNPREPARED",
        "dispatch_count": 0,
        "record_count": 0,
        "verify_count": 0,
        "mutation_count": 0,
        "reason": "invalid-or-conflicting-executor-binding",
    }
    assert not (tmp_path / "store").exists()
