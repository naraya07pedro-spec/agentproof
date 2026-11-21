import json
import subprocess
import sys
from pathlib import Path

import pytest
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


@pytest.mark.parametrize(
    "document",
    [
        'version: "1"\nname: [synthetic-value\n',
        'version: "1"\nname: synthetic-value\ncases: !!int ""\n',
        'version: !!str "1"\nname: synthetic-value\ncases:\n'
        "  - id: ordinary\n"
        "    input: {text: synthetic-value}\n"
        "    expected: {outcome: completed}\n",
        'version: "1"\nname: synthetic-value\ncases: !!seq\n'
        "  - id: ordinary\n"
        "    input: {text: synthetic-value}\n"
        "    expected: {outcome: completed}\n",
    ],
    ids=["malformed", "malformed-tagged-scalar", "tagged-scalar", "tagged-collection"],
)
def test_gate_rejects_malformed_or_tagged_yaml(document: str, tmp_path: Path) -> None:
    suite = tmp_path / "synthetic-suite.yaml"
    suite.write_text(document, encoding="utf-8")

    process = subprocess.run(
        [sys.executable, "-m", "agentproof", "gate", str(suite)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert process.returncode == 2
    assert process.stderr == ""
    assert json.loads(process.stdout) == {
        "error": "invalid_or_unreadable_suite",
        "gate_passed": False,
    }
    assert "synthetic-value" not in process.stdout
    assert str(suite) not in process.stdout
