from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CoverageTarget:
    name: str
    prefix: str
    minimum: float


TARGETS = (
    CoverageTarget("cyoa/core", "cyoa/core/", 83.0),
    CoverageTarget("cyoa/llm", "cyoa/llm/", 78.0),
    CoverageTarget("cyoa/db", "cyoa/db/", 72.0),
    CoverageTarget("cyoa/ui", "cyoa/ui/", 85.0),
)


def load_coverage_report(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Coverage report root must be a JSON object.")
    return data


def summarize_target(
    files: dict[str, object],
    target: CoverageTarget,
) -> tuple[int, int]:
    covered = 0
    statements = 0
    for file_path, raw_summary in files.items():
        if not file_path.startswith(target.prefix):
            continue
        if not isinstance(raw_summary, dict):
            continue
        summary = raw_summary.get("summary")
        if not isinstance(summary, dict):
            continue
        num_statements = summary.get("num_statements")
        missing_lines = summary.get("missing_lines")
        if not isinstance(num_statements, int) or not isinstance(missing_lines, int):
            continue
        statements += num_statements
        covered += num_statements - missing_lines
    return covered, statements


def main() -> int:
    report_path = Path("coverage.json")
    if len(sys.argv) > 1:
        report_path = Path(sys.argv[1])

    data = load_coverage_report(report_path)
    files = data.get("files")
    if not isinstance(files, dict):
        raise ValueError("Coverage report is missing file summaries.")

    failures: list[str] = []

    print("Coverage thresholds")
    for target in TARGETS:
        covered, statements = summarize_target(files, target)
        if statements == 0:
            failures.append(f"{target.name}: no measured statements found")
            print(f"- {target.name}: no measured statements found")
            continue

        percent = covered / statements * 100
        print(
            f"- {target.name}: {percent:.2f}% "
            f"({covered}/{statements} lines, floor {target.minimum:.2f}%)"
        )
        if percent < target.minimum:
            failures.append(
                f"{target.name}: {percent:.2f}% is below {target.minimum:.2f}%"
            )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
