"""
complexity_cli.py — Thin CLI wrapper around the Big-O inference engine.

Invoked by the VS Code extension as a child process:

    uv run python -m app.complexity_cli <absolute_file_path>

Prints a JSON array to stdout and exits 0 on success, 1 on fatal error.
All diagnostic messages go to stderr so stdout stays machine-parseable.
"""

import json
import sys

from app.complexity import infer_file_complexity


def main() -> None:
    if len(sys.argv) != 2:
        print(
            json.dumps([{
                "name": "ERROR",
                "lineno": 0,
                "complexity": "O(?) — Undecidable",
                "detail": "Usage: python -m app.complexity_cli <file_path>",
            }])
        )
        sys.exit(1)

    file_path = sys.argv[1]
    try:
        result = infer_file_complexity(file_path)
        print(json.dumps(result))
        sys.exit(0)
    except Exception as exc:          # pragma: no cover — safety net
        print(
            json.dumps([{
                "name": "ERROR",
                "lineno": 0,
                "complexity": "O(?) — Undecidable",
                "detail": f"Unexpected error: {exc}",
            }])
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
