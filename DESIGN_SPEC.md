# DESIGN_SPEC.md — localcode-optimizer

> **ADK 2.0 Prototype** | Gemini API Key auth | `--prototype` mode (no cloud deployment)

---

## 1. Overview

`localcode-optimizer` is a locally-running ADK 2.0 agent that performs **static code review and refactoring suggestion** on Python source files. It combines deterministic AST-based analysis (fast, accurate, zero-LLM-cost) with Gemini's language understanding to produce actionable, context-aware refactoring recommendations.

**Core value proposition:**
- Runs entirely on the developer's machine (no upload of code to external services beyond the Gemini API call)
- AST-first: issues are detected structurally before asking Gemini, reducing hallucination
- Produces both a structured issue report and a prose refactored code suggestion

---

## 2. Agent Identity

| Property | Value |
|----------|-------|
| Agent name | `localcode_optimizer` |
| Model | `gemini-2.0-flash` |
| Auth | Gemini API Key (`GEMINI_API_KEY`) |
| ADK App name | `app` |
| Entry point | `app/agent.py` → `app` |
| Tools module | `app/tools.py` |

---

## 3. Workflow

```
User Input
  │
  │  "Review /path/to/myfile.py"
  ▼
┌─────────────────────────────────────────┐
│            root_agent (Gemini)          │
│  Reads user intent, chooses tools       │
└────────────────┬────────────────────────┘
                 │
         ┌───────┴────────┐
         ▼                ▼
 parse_python_file   get_file_content
 ─────────────────   ────────────────
 Uses `ast` module   Reads raw source
 Returns structured  Returns source str
 metrics + summary   for Gemini context
         │                │
         └───────┬─────────┘
                 ▼
        extract_code_issues
        ──────────────────
        Walks AST nodes,
        flags anti-patterns
        (see Section 5)
                 │
                 ▼
┌─────────────────────────────────────────┐
│   Gemini synthesizes final response:    │
│   • Numbered issue list (from AST)      │
│   • Severity rating per issue           │
│   • Refactored code snippet per issue   │
│   • Overall quality score (1–10)        │
└─────────────────────────────────────────┘
                 │
                 ▼
          User Response
```

---

## 4. Tool Inventory

### 4.1 `parse_python_file(file_path: str) -> dict`
**Source:** `app/tools.py`

Reads and parses a `.py` file using Python's built-in `ast` module. Returns a JSON-serializable summary dict containing:

| Key | Description |
|-----|-------------|
| `file_path` | Resolved absolute path |
| `line_count` | Total lines in file |
| `functions` | List of `{name, lineno, arg_count, is_async}` |
| `classes` | List of `{name, lineno, method_count, base_count}` |
| `imports` | List of imported module names |
| `max_function_lines` | Longest function body (lines) |
| `avg_function_lines` | Average function length |
| `parse_error` | Error message if parsing failed, else `null` |

**Error handling:** Returns `{"parse_error": "<message>"}` on `SyntaxError` or `FileNotFoundError` so the agent can report failures gracefully.

---

### 4.2 `extract_code_issues(file_path: str) -> list[dict]`
**Source:** `app/tools.py`

Performs a second AST walk focused entirely on anti-pattern detection. Returns a list of issue dicts:

```json
[
  {
    "rule": "LONG_FUNCTION",
    "severity": "warning",
    "lineno": 42,
    "detail": "Function 'process_data' spans 87 lines (threshold: 50)"
  }
]
```

**Detected rules:**

| Rule ID | Severity | Condition |
|---------|----------|-----------|
| `LONG_FUNCTION` | warning | Function body > 50 lines |
| `TOO_MANY_ARGS` | warning | Function has > 5 parameters |
| `BARE_EXCEPT` | error | `except:` with no exception type |
| `MUTABLE_DEFAULT` | error | Default arg is `[]`, `{}`, or `set()` |
| `NESTED_LOOP` | info | Loop nested inside another loop (depth >= 2) |
| `GLOBAL_VAR` | info | Use of `global` statement |
| `PRINT_STATEMENT` | info | `print()` call found (suggests missing logging) |
| `MAGIC_NUMBER` | info | Numeric literal > 9 not assigned to a constant |
| `DEEP_NESTING` | warning | AST nesting depth > 4 |
| `MISSING_DOCSTRING` | info | Public function/class without docstring |

---

### 4.3 `get_file_content(file_path: str) -> str`
**Source:** `app/tools.py`

Reads and returns the raw text content of the file (UTF-8). Gemini uses this as its primary context window input. Includes a safety cap of **500 lines** (returns a truncation notice beyond that) to avoid exceeding model context limits on very large files.

---

## 5. Agent System Instruction

The agent is given the following system instruction (see `app/agent.py`):

```
You are localcode-optimizer, an expert Python code reviewer specialising in
refactoring and optimization. When a user asks you to review a file:

1. Call parse_python_file() to get the structural overview.
2. Call extract_code_issues() to get the AST-detected issues list.
3. Call get_file_content() to read the source code.
4. Synthesize a review report with these sections:
   ## Summary
   ## Detected Issues  (table: rule | severity | line | description)
   ## Refactoring Suggestions  (fenced code blocks with before/after)
   ## Overall Quality Score  (1–10 with rationale)

Be specific and cite exact line numbers. Prefer minimal, targeted
refactoring over large rewrites. Always preserve the original intent.
If parse_python_file returns a parse_error, report the syntax error
and stop — do not attempt further analysis.
```

---

## 6. File Structure

```
localcode-optimizer/
├── DESIGN_SPEC.md          <- this file
├── GEMINI.md               <- agent coding guidance (auto-generated)
├── README.md               <- setup & usage instructions
├── agents-cli-manifest.yaml
├── pyproject.toml
├── Dockerfile
├── .gitignore
├── app/
│   ├── __init__.py         <- exports `app` (do not modify)
│   ├── agent.py            <- Agent definition + tool registration
│   ├── tools.py            <- AST tool implementations  [NEW]
│   └── app_utils/
│       └── ...             <- ADK scaffolded utilities
└── tests/
    └── ...
```

---

## 7. Configuration & Environment

| Variable | Purpose | Required |
|----------|---------|----------|
| `GEMINI_API_KEY` | Authenticates Gemini API calls | Yes |
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `False` for API-key mode | Yes (auto-set) |

No Google Cloud project or `gcloud` credentials are required for the prototype.

---

## 8. Running Locally

```bash
cd localcode-optimizer
agents-cli install          # installs Python deps into .venv
agents-cli playground       # launches the web chat UI at localhost:8080

# Quick smoke test:
agents-cli run "Review the file app/tools.py and give me a quality score"
```

---

## 9. Example Interaction

**User:**
> Review `/home/user/myproject/data_pipeline.py` and suggest optimizations.

**Agent flow:**
1. `parse_python_file("/home/user/myproject/data_pipeline.py")` -> 3 functions, 210 lines
2. `extract_code_issues(...)` -> `BARE_EXCEPT` (line 44), `LONG_FUNCTION` (line 12, 87 lines), `MUTABLE_DEFAULT` (line 7)
3. `get_file_content(...)` -> raw source
4. Gemini generates full review with before/after refactoring for each issue

---

## 10. Future Enhancements (Post-Prototype)

- [ ] Multi-file batch review (`review_directory(path)` tool)
- [ ] Git diff mode — only review changed lines
- [ ] Auto-apply safe refactorings with `--autofix` flag
- [ ] `agents-cli scaffold enhance` -> deploy to Cloud Run for team-wide usage
- [ ] Eval dataset: collect reviewed files + expected issue sets for `agents-cli eval grade`
