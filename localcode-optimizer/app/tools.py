# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
tools.py — AST-based Python code analysis tools for localcode-optimizer.

These tools are pure Python (no LLM calls). They are registered with the
ADK agent in agent.py and invoked by Gemini during a review session.
"""


import ast
import json
import os 
from pathlib import Path

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_LINES = 500          # Truncation cap for get_file_content
LONG_FUNCTION_THRESHOLD = 50  # Lines
TOO_MANY_ARGS_THRESHOLD = 5   # Parameters
MAGIC_NUMBER_THRESHOLD = 9    # Numeric literals above this are flagged
DEEP_NESTING_THRESHOLD = 4    # Max acceptable AST nesting depth


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(file_path: str) -> Path:
    """Return an absolute, resolved Path object."""
    return Path(file_path).expanduser().resolve()


def _function_body_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count the number of source lines in a function body."""
    if not node.body:
        return 0
    first = node.body[0]
    last = node.body[-1]
    start = getattr(first, "lineno", node.lineno)
    end = getattr(last, "end_lineno", getattr(last, "lineno", start))
    return max(0, end - start + 1)


def _max_depth(node: ast.AST, current: int = 0) -> int:
    """Recursively compute the maximum AST nesting depth below *node*."""
    children = list(ast.iter_child_nodes(node))
    if not children:
        return current
    return max(_max_depth(child, current + 1) for child in children)


# ---------------------------------------------------------------------------
# Tool 1: parse_python_file
# ---------------------------------------------------------------------------

def parse_python_file(file_path: str) -> dict[str, Any]:
    """Parse a Python source file and return a structural summary.

    Uses Python's built-in `ast` module to extract functions, classes,
    imports, and basic complexity metrics without executing the code.

    Args:
        file_path: Absolute or relative path to the .py file to analyse.

    Returns:
        A dict with keys:
          - file_path (str): resolved absolute path
          - line_count (int): total lines in the file
          - functions (list[dict]): each has name, lineno, arg_count,
                                     body_lines, is_async
          - classes (list[dict]): each has name, lineno, method_count,
                                   base_count
          - imports (list[str]): top-level imported module names
          - max_function_lines (int): longest function body in lines
          - avg_function_lines (float): average function body length
          - parse_error (str | None): error message if parsing failed
    """
    path = _resolve(file_path)

    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"parse_error": f"File not found: {path}"}
    except PermissionError:
        return {"parse_error": f"Permission denied: {path}"}

    lines = source.splitlines()
    line_count = len(lines)

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "parse_error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
            "file_path": str(path),
            "line_count": line_count,
        }

    functions: list[dict] = []
    classes: list[dict] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = _function_body_lines(node)
            functions.append({
                "name": node.name,
                "lineno": node.lineno,
                "arg_count": len(node.args.args),
                "body_lines": body_lines,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })

        elif isinstance(node, ast.ClassDef):
            method_count = sum(
                1 for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            classes.append({
                "name": node.name,
                "lineno": node.lineno,
                "method_count": method_count,
                "base_count": len(node.bases),
            })

        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)

        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    body_lengths = [f["body_lines"] for f in functions]
    max_fn_lines = max(body_lengths, default=0)
    avg_fn_lines = round(sum(body_lengths) / len(body_lengths), 1) if body_lengths else 0.0

    return {
        "file_path": str(path),
        "line_count": line_count,
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "max_function_lines": max_fn_lines,
        "avg_function_lines": avg_fn_lines,
        "parse_error": None,
    }


# ---------------------------------------------------------------------------
# Tool 2: extract_code_issues
# ---------------------------------------------------------------------------

def extract_code_issues(file_path: str) -> list[dict[str, Any]]:
    """Walk the AST of a Python file and detect common anti-patterns.

    Performs a thorough structural analysis without executing the code,
    returning a list of flagged issues with severity and line numbers.

    Args:
        file_path: Absolute or relative path to the .py file to analyse.

    Returns:
        A list of issue dicts, each with:
          - rule (str): rule identifier (e.g. "BARE_EXCEPT")
          - severity (str): "error" | "warning" | "info"
          - lineno (int): source line where the issue was detected
          - detail (str): human-readable description of the issue

        Returns [{"rule": "PARSE_ERROR", ...}] if the file cannot be parsed.
    """
    path = _resolve(file_path)

    try:
        source = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError) as exc:
        return [{"rule": "IO_ERROR", "severity": "error", "lineno": 0, "detail": str(exc)}]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [{
            "rule": "PARSE_ERROR",
            "severity": "error",
            "lineno": exc.lineno or 0,
            "detail": f"SyntaxError: {exc.msg}",
        }]

    issues: list[dict[str, Any]] = []

    # --- LONG_FUNCTION / TOO_MANY_ARGS / MISSING_DOCSTRING (functions) ---
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = _function_body_lines(node)
            arg_count = len(node.args.args)
            fn_name = node.name

            if body_lines > LONG_FUNCTION_THRESHOLD:
                issues.append({
                    "rule": "LONG_FUNCTION",
                    "severity": "warning",
                    "lineno": node.lineno,
                    "detail": (
                        f"Function '{fn_name}' spans {body_lines} lines "
                        f"(threshold: {LONG_FUNCTION_THRESHOLD})"
                    ),
                })

            if arg_count > TOO_MANY_ARGS_THRESHOLD:
                issues.append({
                    "rule": "TOO_MANY_ARGS",
                    "severity": "warning",
                    "lineno": node.lineno,
                    "detail": (
                        f"Function '{fn_name}' has {arg_count} parameters "
                        f"(threshold: {TOO_MANY_ARGS_THRESHOLD}); "
                        "consider using a dataclass or **kwargs"
                    ),
                })

            if not fn_name.startswith("_"):
                first_stmt = node.body[0] if node.body else None
                has_doc = isinstance(first_stmt, ast.Expr) and isinstance(
                    getattr(first_stmt, "value", None), ast.Constant
                )
                if not has_doc:
                    issues.append({
                        "rule": "MISSING_DOCSTRING",
                        "severity": "info",
                        "lineno": node.lineno,
                        "detail": f"Public function '{fn_name}' has no docstring",
                    })

            for default in node.args.defaults + node.args.kw_defaults:
                if default is None:
                    continue
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    issues.append({
                        "rule": "MUTABLE_DEFAULT",
                        "severity": "error",
                        "lineno": node.lineno,
                        "detail": (
                            f"Function '{fn_name}' uses a mutable default argument "
                            f"({type(default).__name__}); use None and initialise inside"
                        ),
                    })

    # --- BARE_EXCEPT ---
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({
                "rule": "BARE_EXCEPT",
                "severity": "error",
                "lineno": node.lineno,
                "detail": "Bare `except:` catches all exceptions including SystemExit; specify the exception type",
            })

    # --- GLOBAL_VAR ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            issues.append({
                "rule": "GLOBAL_VAR",
                "severity": "info",
                "lineno": node.lineno,
                "detail": f"Use of `global` for: {', '.join(node.names)}; prefer passing state explicitly",
            })

    # --- PRINT_STATEMENT ---
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            issues.append({
                "rule": "PRINT_STATEMENT",
                "severity": "info",
                "lineno": node.lineno,
                "detail": "`print()` found; consider replacing with `logging` for production code",
            })

    # --- NESTED_LOOP ---
    # Iterative DFS: avoids recursion-limit risk on deeply nested files, O(n).
    loop_stack: list[tuple[ast.AST, int]] = [(tree, 0)]  # (node, loop_depth)
    while loop_stack:
        current, loop_depth = loop_stack.pop()
        is_loop_node = isinstance(current, (ast.For, ast.While))
        if is_loop_node and loop_depth >= 1:
            issues.append({
                "rule": "NESTED_LOOP",
                "severity": "info",
                "lineno": current.lineno,
                "detail": (
                    f"Nested loop detected (depth {loop_depth + 1}); "
                    "may indicate O(n\u00b2) complexity"
                ),
            })
        next_depth = loop_depth + 1 if is_loop_node else loop_depth
        loop_stack.extend(
            (child, next_depth) for child in ast.iter_child_nodes(current)
        )

    # --- MAGIC_NUMBER ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            val = node.value
            if isinstance(val, bool):
                continue
            if abs(val) > MAGIC_NUMBER_THRESHOLD:
                issues.append({
                    "rule": "MAGIC_NUMBER",
                    "severity": "info",
                    "lineno": node.lineno,
                    "detail": (
                        f"Magic number `{val}` found; extract to a named constant for readability"
                    ),
                })

    # --- DEEP_NESTING ---
    depth = _max_depth(tree)
    if depth > DEEP_NESTING_THRESHOLD:
        issues.append({
            "rule": "DEEP_NESTING",
            "severity": "warning",
            "lineno": 1,
            "detail": (
                f"File has AST nesting depth of {depth} "
                f"(threshold: {DEEP_NESTING_THRESHOLD}); consider extracting helper functions"
            ),
        })

    issues.sort(key=lambda x: x["lineno"])
    return issues


# ---------------------------------------------------------------------------
# Tool 3: get_file_content
# ---------------------------------------------------------------------------

def get_file_content(file_path: str) -> str:
    """Read and return the raw source code of a Python file.

    Caps output at MAX_FILE_LINES lines to avoid overflowing the model's
    context window. A truncation notice is appended when the cap is hit.

    Args:
        file_path: Absolute or relative path to the .py file.

    Returns:
        The UTF-8 text content of the file, or an error message string.
    """
    path = _resolve(file_path)

    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"ERROR: File not found: {path}"
    except PermissionError:
        return f"ERROR: Permission denied: {path}"

    lines = source.splitlines()
    if len(lines) > MAX_FILE_LINES:
        truncated = "\n".join(lines[:MAX_FILE_LINES])
        notice = (
            f"\n\n# --- TRUNCATED: showing first {MAX_FILE_LINES} of "
            f"{len(lines)} lines ---"
        )
        return truncated + notice

    return source
