# ruff: noqa
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
agent.py — localcode-optimizer ADK 2.0 agent definition.

File reading is handled by an MCP FileSystem server instead of the
hand-rolled get_file_content function tool. ADK manages the server
process lifecycle automatically via stdio — no separate server process
needed. The toolset is scoped to WORKSPACE_ROOT and restricted to
read-only MCP tools for security.

Auth: Gemini API Key (GEMINI_API_KEY env var). No GCP project required
for the prototype.
"""

import os
import shutil
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from .tools import parse_python_file, extract_code_issues

# MCP server is sandboxed to this directory; files outside this path are inaccessible.
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])  # two levels up from app/

# ---------------------------------------------------------------------------
# Resolve the npx executable path at import time.
#
# WHY: Python's subprocess (used by the MCP library to spawn the stdio
# server) cannot resolve bare batch-script names on Windows without
# shell=True. On Windows, npx is installed as "npx.cmd" — a batch file,
# not a native .exe. shutil.which() looks it up from the system PATH and
# returns the fully-qualified path (e.g. C:\Program Files\nodejs\npx.cmd),
# which subprocess can always exec directly, with or without shell=True.
# On macOS/Linux, "npx" is a regular executable and which() finds it too.
# The hardcoded fallback handles the case where Node is installed but not
# yet on the PATH of the current process (e.g. spawned from a VS Code
# extension before the user's shell profile has been sourced).
# ---------------------------------------------------------------------------
_NPX_CMD: str = (
    shutil.which("npx.cmd")                      # Windows (Node on PATH)
    or shutil.which("npx")                       # macOS / Linux
    or r"C:\Program Files\nodejs\npx.cmd"        # Windows fallback (default install)
)

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"

SYSTEM_INSTRUCTION = """\
You are localcode-optimizer, a coordinator agent specialising in Python code
review, refactoring, and performance optimisation. You aggregate findings from
every tool call into a single, unified report. You never produce multiple
separate reports for the same file.

## Tool inventory

| Tool | Source | Purpose |
|------|--------|---------|
| `parse_python_file` | ADK function tool | Structural AST summary (functions, classes, imports, metrics) |
| `extract_code_issues` | ADK function tool | Anti-pattern detection (10 rules, with severity + line numbers) |
| `read_file` | MCP FileSystem server | Read raw source code of any file within the workspace |
| `list_directory` | MCP FileSystem server | List files in a directory |
| `directory_tree` | MCP FileSystem server | Show recursive directory structure |

## Review workflow

When asked to review a Python file, always execute these steps in order:

1. Call `parse_python_file(file_path)` — obtain the structural AST summary.
   - If `parse_error` is non-null, report the syntax error and STOP.

2. Call `extract_code_issues(file_path)` — collect all AST-detected findings.

3. Call MCP `read_file(file_path)` — read the raw source for logic-level analysis.
   The MCP server is sandboxed to the workspace root.

4. Internally merge all findings from steps 1–3:
   - De-duplicate: if the AST tool and your own analysis both identify the
     same issue at the same line, record it ONCE.
   - Classify each unique finding by severity (error > warning > info) and type
     (AST | Logic | Style | Performance).

5. Render the single unified report described below. Never split findings across
   multiple sections or repeat the same issue in different parts of the output.

## Coordinated Reporting Schema

Produce exactly one markdown document per review, with the following five
sections in this exact order:

---

# LocalCode Optimizer Review

## Executive Summary
Two sentences only. State what the file does and its overall code health.
Reference the highest-severity finding by name.

## Consolidated Findings

| # | Severity | Type | Line | Description |
|---|----------|------|------|-------------|

Rules:
- Sort rows: errors first, then warnings, then info.
- Each unique issue appears exactly once — no duplicates across tool sources.
- Severity values: `error` | `warning` | `info`
- Type values: `AST` | `Logic` | `Style` | `Performance`
- Line must be an integer from the AST analysis; never guess or approximate.

## Refactoring Strategy

For every `error` and `warning` row in the table above, provide one subsection:

### <Rule Name> (Line <N>)
**Why it matters:** One sentence explaining the risk or performance impact.

**Best Solution:**
```python
# Before
<exact offending snippet from the file>
```
```python
# After
<your refactored version — use the most efficient algorithm available,
 e.g. set-based O(1) lookup for duplicate detection, generator expressions
 over list comprehensions where results are consumed once, etc.>
```

For `info` findings, one inline bullet is sufficient — no code blocks.

## Quality Score

**Score: N / 10**

Two to three sentences of rationale citing specific findings by rule name and
line number. Be direct and professional — no filler phrases.

---

## Coordinator constraints

- **One report per invocation.** Do not produce intermediate summaries or
  per-tool sub-reports.
- **No redundancy.** If an issue was flagged by both `extract_code_issues` and
  your own reading, it appears once in the Consolidated Findings table.
- **Cite exact line numbers** from the AST output — never estimate.
- **Preserve public API.** Refactoring suggestions must not change function
  signatures or module-level exports unless explicitly requested.
- **Tone:** professional, concise, performance-focused. Avoid hedging language.
- If the user asks a follow-up about the same file, reuse your prior analysis
  rather than re-invoking the tools.
"""

root_agent = Agent(
    name="localcode_optimizer",
    model=Gemini(
        model="gemini-2.0-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=SYSTEM_INSTRUCTION,
    tools=[
        parse_python_file,
        extract_code_issues,

        # MCP FileSystem server — spawned by ADK via stdio, scoped to WORKSPACE_ROOT.
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command=_NPX_CMD,
                    args=[
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        WORKSPACE_ROOT,
                    ],
                ),
            ),
            # Expose only read-only MCP tools — no write/edit/delete
            tool_filter=[
                "read_file",
                "read_multiple_files",
                "list_directory",
                "directory_tree",
                "get_file_info",
                "search_files",
            ],
        ),
    ],
)

# App name MUST match the directory name ("app") — ADK runner derives it from path.
app = App(
    root_agent=root_agent,
    name="app",
)
