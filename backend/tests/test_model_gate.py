import json

import pytest
from pytest import CaptureFixture

from agentproof.cli import main


def test_model_gate_external_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AGENTPROOF_MODEL_ENABLE_EXTERNAL", raising=False)
    monkeypatch.setenv("AGENTPROOF_MODEL_TOKEN", "synthetic-value")

    assert main(["model-gate", "--agent", "external"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "error": "model_gate_configuration_invalid",
        "gate_passed": False,
    }
    assert captured.err == ""
