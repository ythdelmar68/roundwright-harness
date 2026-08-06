from __future__ import annotations

import json

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
