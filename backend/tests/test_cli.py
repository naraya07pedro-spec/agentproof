import json
from pathlib import Path

from pytest import CaptureFixture

from agentproof.cli import main

SUITE = Path(__file__).resolve().parents[2] / "examples" / "defensive-suite.json"


def test_gate_exit_status_matches_json(capsys: CaptureFixture[str]) -> None:
    assert main(["gate", str(SUITE), "--fixture", "corrected"]) == 0
    corrected = json.loads(capsys.readouterr().out)
    assert corrected["gate_passed"] is True

    assert main(["gate", str(SUITE), "--fixture", "vulnerable"]) == 1
    vulnerable = json.loads(capsys.readouterr().out)
    assert vulnerable["gate_passed"] is False
    assert vulnerable["critical_failed"]


def test_gate_sanitizes_input_error(capsys: CaptureFixture[str], tmp_path: Path) -> None:
    suite = tmp_path / "bad.json"
    suite.write_text('{"private-value": null}')
    assert main(["gate", str(suite)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_or_unreadable_suite",
        "gate_passed": False,
    }
