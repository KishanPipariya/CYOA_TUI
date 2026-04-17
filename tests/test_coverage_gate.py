import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "check_coverage.py"
    spec = importlib.util.spec_from_file_location("check_coverage", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_check_coverage_passes_when_targets_meet_thresholds(tmp_path, monkeypatch):
    module = _load_module()
    report_path = tmp_path / "coverage.json"
    report_path.write_text(
        json.dumps(
            {
                "files": {
                    "cyoa/core/engine.py": {
                        "summary": {"num_statements": 100, "missing_lines": 10}
                    },
                    "cyoa/llm/broker.py": {
                        "summary": {"num_statements": 100, "missing_lines": 20}
                    },
                    "cyoa/db/graph_db.py": {
                        "summary": {"num_statements": 100, "missing_lines": 30}
                    },
                    "cyoa/ui/app.py": {
                        "summary": {"num_statements": 100, "missing_lines": 15}
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.sys, "argv", ["check_coverage.py", str(report_path)])

    assert module.main() == 0


def test_check_coverage_fails_when_target_is_below_threshold(tmp_path, monkeypatch, capsys):
    module = _load_module()
    report_path = tmp_path / "coverage.json"
    report_path.write_text(
        json.dumps(
            {
                "files": {
                    "cyoa/core/engine.py": {
                        "summary": {"num_statements": 100, "missing_lines": 25}
                    },
                    "cyoa/llm/broker.py": {
                        "summary": {"num_statements": 100, "missing_lines": 20}
                    },
                    "cyoa/db/graph_db.py": {
                        "summary": {"num_statements": 100, "missing_lines": 20}
                    },
                    "cyoa/ui/app.py": {
                        "summary": {"num_statements": 100, "missing_lines": 16}
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module.sys, "argv", ["check_coverage.py", str(report_path)])

    assert module.main() == 1
    assert "FAIL: cyoa/core" in capsys.readouterr().err
