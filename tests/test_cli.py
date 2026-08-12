from __future__ import annotations

import json
from pathlib import Path

from roundwright_harness import cli


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
