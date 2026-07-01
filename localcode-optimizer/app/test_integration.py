"""
test_integration.py - Backend integration validation for the Docker container.

Run inside the container with:
    uv run python -m app.test_integration

Exit code 0 = all checks passed.
Exit code 1 = one or more checks failed.
"""

import os
import sys

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [{PASS}] {label}")
    else:
        msg = f"{label}" + (f" -- {detail}" if detail else "")
        print(f"  [{FAIL}] {msg}")
        failures.append(msg)


# ---------------------------------------------------------------------------
# 1. Environment variables
# ---------------------------------------------------------------------------
print("\n-- Environment -----------------------------------------------------")

gemini_key = os.environ.get("GEMINI_API_KEY", "")
check("GEMINI_API_KEY is set", bool(gemini_key), "env var is empty or missing")

use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "")
check(
    "GOOGLE_GENAI_USE_VERTEXAI == 'False'",
    use_vertex == "False",
    f"got '{use_vertex}'",
)

# ---------------------------------------------------------------------------
# 2. app.agent imports and core objects
# ---------------------------------------------------------------------------
print("\n-- app.agent -------------------------------------------------------")

try:
    from app.agent import app, root_agent, _NPX_CMD, WORKSPACE_ROOT

    check("app.agent imports cleanly", True)
    check("`app` object exists", app is not None)
    check("`root_agent` object exists", root_agent is not None)
    check(
        "`_NPX_CMD` resolved to a non-empty path",
        bool(_NPX_CMD),
        f"got '{_NPX_CMD}'",
    )
    check(
        "WORKSPACE_ROOT is an existing directory",
        os.path.isdir(WORKSPACE_ROOT),
        f"path '{WORKSPACE_ROOT}' not found",
    )
    print(f"       _NPX_CMD       = {_NPX_CMD}")
    print(f"       WORKSPACE_ROOT = {WORKSPACE_ROOT}")
except Exception as exc:
    check("app.agent imports cleanly", False, str(exc))
    check("`app` object exists", False, "import failed")
    check("`root_agent` object exists", False, "import failed")
    check("`_NPX_CMD` resolved to a non-empty path", False, "import failed")
    check("WORKSPACE_ROOT is an existing directory", False, "import failed")

# ---------------------------------------------------------------------------
# 3. app.tools imports and callable surface
# ---------------------------------------------------------------------------
print("\n-- app.tools -------------------------------------------------------")

try:
    from app.tools import parse_python_file, extract_code_issues, get_file_content

    check("app.tools imports cleanly", True)
    check("`parse_python_file` is callable", callable(parse_python_file))
    check("`extract_code_issues` is callable", callable(extract_code_issues))
    check("`get_file_content` is callable", callable(get_file_content))
except Exception as exc:
    check("app.tools imports cleanly", False, str(exc))

# ---------------------------------------------------------------------------
# 4. Functional smoke -- parse and analyse this file itself
# ---------------------------------------------------------------------------
print("\n-- Functional smoke (tools on this file) ---------------------------")

THIS_FILE = os.path.abspath(__file__)

try:
    result = parse_python_file(THIS_FILE)
    check(
        "parse_python_file() returns no parse_error",
        result.get("parse_error") is None,
        result.get("parse_error", ""),
    )
    check(
        "parse_python_file() returns a line_count > 0",
        result.get("line_count", 0) > 0,
        f"got {result.get('line_count')}",
    )
except Exception as exc:
    check("parse_python_file() executes without exception", False, str(exc))

try:
    issues = extract_code_issues(THIS_FILE)
    check(
        "extract_code_issues() returns a list",
        isinstance(issues, list),
        f"got {type(issues).__name__}",
    )
except Exception as exc:
    check("extract_code_issues() executes without exception", False, str(exc))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n--------------------------------------------------------------------")
if failures:
    print(f"  RESULT: {len(failures)} check(s) FAILED\n")
    for f in failures:
        print(f"    x {f}")
    print()
    sys.exit(1)
else:
    print("  RESULT: ALL CHECKS PASSED\n")
    sys.exit(0)
