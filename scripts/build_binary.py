import argparse
import os
import subprocess
from pathlib import Path

APP_NAME = "cyoa-tui"


def _data_separator() -> str:
    return ";" if os.name == "nt" else ":"


def _format_add_data(source: Path, target: str) -> str:
    return f"{source}{_data_separator()}{target}"


def build_pyinstaller_command(
    *,
    project_root: Path,
    dist_dir: Path,
    build_dir: Path,
) -> list[str]:
    return [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--add-data",
        _format_add_data(project_root / "themes", "themes"),
        "--add-data",
        _format_add_data(project_root / "cyoa" / "llm" / "templates", "cyoa/llm/templates"),
        "--add-data",
        _format_add_data(project_root / "cyoa" / "ui" / "styles.tcss", "cyoa/ui"),
        "--collect-data",
        "textual",
        str(project_root / "main.py"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone macOS/Linux terminal bundle with PyInstaller."
    )
    parser.add_argument(
        "--dist-dir",
        default="dist/pyinstaller",
        help="Output directory for packaged builds (default: dist/pyinstaller).",
    )
    parser.add_argument(
        "--build-dir",
        default="build/pyinstaller",
        help="Working directory for PyInstaller intermediates (default: build/pyinstaller).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    dist_dir = (project_root / args.dist_dir).resolve()
    build_dir = (project_root / args.build_dir).resolve()
    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    command = build_pyinstaller_command(
        project_root=project_root,
        dist_dir=dist_dir,
        build_dir=build_dir,
    )
    print("Running:", " ".join(command))
    subprocess.run(command, check=True)
    print(f"Packaged app written to {dist_dir / APP_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
