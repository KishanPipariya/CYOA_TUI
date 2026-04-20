from pathlib import Path
from types import SimpleNamespace

from cyoa.core.preflight import (
    DISK_HEADROOM_GB,
    check_local_model_preflight,
    check_terminal_conditions,
)


def test_terminal_preflight_warns_for_small_terminal() -> None:
    report = check_terminal_conditions(width=80, height=24, term="xterm-256color")

    assert report.has_blocking_issues is False
    assert any("80x24" in line for line in report.render_lines())


def test_terminal_preflight_blocks_dumb_term() -> None:
    report = check_terminal_conditions(width=120, height=40, term="dumb")

    assert report.has_blocking_issues is True
    assert "modern terminal emulator" in (report.blocking_reason or "")


def test_local_model_preflight_blocks_for_low_disk(monkeypatch) -> None:
    monkeypatch.setattr("cyoa.core.preflight._free_disk_gb", lambda _path: 1.0)
    recommendation = SimpleNamespace(
        label="7B",
        ram_gb=16.0,
        minimum_ram_gb=12.0,
        approx_size_gb=5.0,
    )

    report = check_local_model_preflight(
        recommendation,
        models_dir=Path("/tmp/cyoa-models"),
        width=120,
        height=40,
        term="xterm-256color",
    )

    assert report.has_blocking_issues is True
    assert f"{5.0 + DISK_HEADROOM_GB:.1f} GB" in (report.blocking_reason or "")


def test_local_model_preflight_reports_success(monkeypatch) -> None:
    monkeypatch.setattr("cyoa.core.preflight._free_disk_gb", lambda _path: 50.0)
    recommendation = SimpleNamespace(
        label="3B",
        ram_gb=12.0,
        minimum_ram_gb=8.0,
        approx_size_gb=2.5,
    )

    report = check_local_model_preflight(
        recommendation,
        models_dir=Path("/tmp/cyoa-models"),
        width=120,
        height=40,
        term="xterm-256color",
    )

    assert report.has_blocking_issues is False
    assert report.render_lines() == [
        "Machine check passed for 3B: 12.0 GB RAM detected and about 50.0 GB disk free. "
        "The model will be stored in your app data folder for future launches."
    ]
