"""
complexity.py — AST-based asymptotic complexity (Big-O) inference engine.

Performs purely static analysis on Python function definitions to estimate
worst-case time complexity without executing any code. Supports:

  - Iterative loops (for / while) with arbitrary nesting depth
  - Logarithmic loop patterns (i //= 2, i >>= 1, i *= 2 inside while)
  - Self-recursive functions with linear or logarithmic recurrences
  - Composite cases that exceed static decidability → O(?) — Undecidable
"""

import ast
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Complexity label constants (ordered by growth rate)
# ---------------------------------------------------------------------------

O_CONST   = "O(1)"
O_LOG     = "O(log n)"
O_LINEAR  = "O(n)"
O_NLOGN   = "O(n log n)"
O_QUAD    = "O(n²)"
O_CUBIC   = "O(n³)"
O_UNKNOWN = "O(?) — Undecidable"

# ---------------------------------------------------------------------------
# Internal pattern detectors
# ---------------------------------------------------------------------------

def _has_log_loop_pattern(loop_node: ast.While) -> bool:
    """
    Return True if a while-loop body contains a halving or doubling assignment
    on its control variable — the hallmark of a logarithmic iteration count.

    Recognised patterns:
      i //= 2   (AugAssign + FloorDiv)
      i >>= 1   (AugAssign + RShift)
      i = i // 2  (Assign + BinOp + FloorDiv)
      i = i >> 1  (Assign + BinOp + RShift)
    """
    for node in ast.walk(loop_node):
        if isinstance(node, ast.AugAssign):
            if isinstance(node.op, (ast.FloorDiv, ast.RShift)):
                return True
        elif isinstance(node, ast.Assign):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.BinOp) and isinstance(
                    sub.op, (ast.FloorDiv, ast.RShift)
                ):
                    return True
    return False


def _has_log_recursion_arg(call_args: list[ast.expr]) -> bool:
    """
    Return True if any argument to a recursive call contains a floor-division
    or right-shift expression — e.g. recursive_fn(n // 2) — which indicates
    a T(n) = T(n/2) + O(1) recurrence resolving to O(log n) by the Master
    Theorem (case 2, a=1, b=2, f(n)=O(1)).
    """
    for arg in call_args:
        for node in ast.walk(arg):
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.FloorDiv, ast.RShift)
            ):
                return True
    return False

# ---------------------------------------------------------------------------
# Per-function visitor
# ---------------------------------------------------------------------------

class _FunctionComplexityVisitor(ast.NodeVisitor):
    """
    Single-pass AST visitor scoped to one function body. Computes:

      max_loop_depth   — deepest loop nesting level (1 = single loop)
      has_log_loop     — whether any while-loop exhibits a halving pattern
      has_recursion    — whether the function calls itself
      has_log_recursion — whether the recursive call passes n//2 (or similar)
    """

    def __init__(self, func_name: str) -> None:
        self.func_name = func_name
        self.max_loop_depth: int = 0
        self.has_log_loop: bool = False
        self.has_recursion: bool = False
        self.has_log_recursion: bool = False
        self._depth: int = 0

    # -- Loop tracking -------------------------------------------------------

    def _enter_loop(self, node: ast.AST) -> None:
        self._depth += 1
        self.max_loop_depth = max(self.max_loop_depth, self._depth)
        if isinstance(node, ast.While) and _has_log_loop_pattern(node):
            self.has_log_loop = True
        self.generic_visit(node)
        self._depth -= 1

    def visit_For(self, node: ast.For) -> None:       # type: ignore[override]
        self._enter_loop(node)

    def visit_While(self, node: ast.While) -> None:   # type: ignore[override]
        self._enter_loop(node)

    # -- Recursion detection -------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:     # type: ignore[override]
        func = node.func
        # Direct self-call:   func_name(...)
        is_direct = isinstance(func, ast.Name) and func.id == self.func_name
        # Method self-call:   self.func_name(...)
        is_method = (
            isinstance(func, ast.Attribute) and func.attr == self.func_name
        )
        if is_direct or is_method:
            self.has_recursion = True
            if _has_log_recursion_arg(node.args):
                self.has_log_recursion = True
        self.generic_visit(node)

# ---------------------------------------------------------------------------
# Inference rules
# ---------------------------------------------------------------------------

def _infer_complexity(v: _FunctionComplexityVisitor) -> str:
    """
    Map visitor measurements to a Big-O label using the following decision tree:

      recursion + loops          → O(?) (hybrid recurrence — undecidable here)
      recursion, log arg         → O(log n)  [Master Theorem case 2]
      recursion only             → O(n)      [T(n)=T(n-1)+O(1), conservative]
      no loops, no recursion     → O(1)
      depth 1, log loop          → O(log n)
      depth 1                    → O(n)
      depth 2, log loop          → O(n log n)
      depth 2                    → O(n²)
      depth 3                    → O(n³)
      depth ≥ 4                  → O(?)
    """
    d = v.max_loop_depth
    rec = v.has_recursion

    if rec and d >= 1:
        return O_UNKNOWN          # mixed: beyond simple Master Theorem

    if rec:
        return O_LOG if v.has_log_recursion else O_LINEAR

    if d == 0:
        return O_CONST
    if d == 1:
        return O_LOG if v.has_log_loop else O_LINEAR
    if d == 2:
        return O_NLOGN if v.has_log_loop else O_QUAD
    if d == 3:
        return O_CUBIC

    return O_UNKNOWN              # depth ≥ 4


def _build_detail(v: _FunctionComplexityVisitor, complexity: str) -> str:
    """Return a one-sentence human-readable explanation of the inference."""
    if complexity == O_CONST:
        return "No loops or recursion detected; runs in constant time."
    if complexity == O_LOG:
        if v.has_log_loop:
            return (
                "While-loop with halving/doubling pattern (//= or >>=); "
                "iterates ⌊log₂ n⌋ times."
            )
        return (
            "Recursive call passes n//2 (or n>>1); "
            "T(n)=T(n/2)+O(1) resolves to O(log n) by the Master Theorem."
        )
    if complexity == O_LINEAR:
        if v.has_recursion:
            return (
                "Linear recursion with no halving; T(n)=T(n-1)+O(1) → O(n) "
                "(conservative estimate)."
            )
        return f"Single loop over input (max depth 1); scales linearly with n."
    if complexity == O_NLOGN:
        return (
            "Outer loop over n with a logarithmic inner step; "
            "typical of sorting-with-binary-search patterns."
        )
    if complexity == O_QUAD:
        return (
            f"Two nested loops detected (max depth {v.max_loop_depth}); "
            "O(n²) for input of size n."
        )
    if complexity == O_CUBIC:
        return (
            f"Three nested loops detected (max depth {v.max_loop_depth}); "
            "O(n³) — consider algorithmic restructuring."
        )
    # O_UNKNOWN
    if v.has_recursion and v.max_loop_depth >= 1:
        return (
            "Mixed recursion and iteration; recurrence cannot be resolved "
            "by static inspection alone."
        )
    return (
        f"Loop nesting depth {v.max_loop_depth} exceeds safe inference "
        "threshold (≥4); manual analysis required."
    )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_file_complexity(file_path: str) -> list[dict[str, Any]]:
    """
    Parse a Python source file and infer worst-case time complexity for every
    function and method definition found at any nesting level.

    Args:
        file_path: Absolute or relative path to the .py source file.

    Returns:
        A list of dicts — one per function — each containing:
          name       (str): function name
          lineno     (int): line number of the ``def`` statement (1-indexed)
          complexity (str): inferred Big-O label (e.g. "O(n²)")
          detail     (str): one-sentence explanation of the inference
    """
    path = Path(file_path).expanduser().resolve()

    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [{"name": "ERROR", "lineno": 0,
                 "complexity": O_UNKNOWN, "detail": f"File not found: {path}"}]
    except PermissionError:
        return [{"name": "ERROR", "lineno": 0,
                 "complexity": O_UNKNOWN, "detail": f"Permission denied: {path}"}]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [{"name": "SYNTAX_ERROR", "lineno": exc.lineno or 0,
                 "complexity": O_UNKNOWN,
                 "detail": f"SyntaxError at line {exc.lineno}: {exc.msg}"}]

    results: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        visitor = _FunctionComplexityVisitor(node.name)
        for child in node.body:          # visit body only, not the signature
            visitor.visit(child)

        complexity = _infer_complexity(visitor)
        results.append({
            "name": node.name,
            "lineno": node.lineno,
            "complexity": complexity,
            "detail": _build_detail(visitor, complexity),
        })

    # Return in source order
    results.sort(key=lambda r: r["lineno"])
    return results
