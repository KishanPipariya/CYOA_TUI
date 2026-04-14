from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cyoa.core.theme_loader import ThemeValidationError, validate_all_themes


def main() -> int:
    try:
        theme_names = validate_all_themes()
    except (OSError, ValueError, ThemeValidationError) as exc:
        print(f"Theme validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Validated {len(theme_names)} theme(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
