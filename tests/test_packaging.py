from pathlib import Path

from scripts.build_binary import build_pyinstaller_command


def test_build_pyinstaller_command_includes_required_assets() -> None:
    project_root = Path("/tmp/cyoa-project")
    command = build_pyinstaller_command(
        project_root=project_root,
        dist_dir=project_root / "dist/pyinstaller",
        build_dir=project_root / "build/pyinstaller",
    )

    assert command[:6] == [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
    ]
    assert "textual" in command
    assert str(project_root / "main.py") == command[-1]
    assert any("themes" in part for part in command)
    assert any("cyoa/llm/templates" in part for part in command)
    assert any("cyoa/ui/styles.tcss" in part for part in command)
