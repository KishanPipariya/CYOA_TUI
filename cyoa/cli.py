from collections.abc import Sequence

from main import main as _main


def main(argv: Sequence[str] | None = None) -> int:
    return _main(argv)
