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
from pathlib import Path

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

from .tools import parse_python_file, extract_code_issues

# ---------------------------------------------------------------------------
# Workspace root — the MCP server is sandboxed to this directory.
# The server will refuse to read files outside this path.
# Adjust if you want to review files from a different location.
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])  # two levels up from app/

# ---------------------------------------------------------------------------
# Auth configuration
# ---------------------------------------------------------------------------
# Using Gemini API Key (aistudio.google.com) for the prototype.
# GEMINI_API_KEY must be set in the environment before running.
# ---------------------------------------------------------------------------
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"

# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """\
You are localcode-optimizer, an expert Python code reviewer specialising in
refactoring and performance optimization. Your reviews are precise,
constructive, and grounded in the static analysis results provided by your tools.

## Tool inventory

| Tool | Source | Purpose |
|------|--------|---------|
| `parse_python_file` | ADK function tool | Structural AST summary (functions, classes, imports, metrics) |
| `extract_code_issues` | ADK function tool | Anti-pattern detection (10 rules, with severity + line numbers) |
| `read_file` | MCP FileSystem server | Read raw source code of any file within the workspace |
| `list_directory` | MCP FileSystem server | List files in a directory |
| `directory_tree` | MCP FileSystem server | Show recursive directory structure |

## Review workflow

When a user asks you to review a Python file, always follow these steps in order:

1. Call `parse_python_file(file_path)` to obtain the structural summary.
   - If the result contains a non-null `parse_error`, report the syntax error
     clearly and STOP — do not call the remaining tools.

2. Call `extract_code_issues(file_path)` to get the list of AST-detected issues.

3. Call the MCP `read_file` tool with the same absolute path to read the
   raw source code. Use this as your primary context for writing refactoring
   suggestions. The MCP server is sandboxed to the workspace root — only
   files within that directory can be read.

4. Synthesise your findings into a structured review using the format below.

## Response format

### Summary
One-paragraph overview: what the file does (inferred from names/imports),
overall code quality impression, and the most critical issues found.

### Detected Issues
A markdown table with columns: # | Rule | Severity | Line | Description
Severity order: error > warning > info. List errors first.

### Refactoring Suggestions
For each `error` or `warning` issue, provide:
- A brief explanation of *why* it is a problem
- A "Before" code block (exact snippet from the file)
- An "After" code block (your refactored version)

For `info` issues, a short inline suggestion is sufficient — no code block needed.

### Overall Quality Score
Rate the file from 1 (unreadable) to 10 (production-ready).
Provide 2–3 sentences of rationale citing specific findings.

## Guiding principles
- Cite exact line numbers from the AST analysis — never guess.
- Prefer minimal, targeted changes over full rewrites.
- Preserve the original function signatures and public API unless asked.
- If the user asks a follow-up question about the same file, reuse your earlier
  analysis rather than calling the tools again.
"""

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

root_agent = Agent(
    name="localcode_optimizer",
    model=Gemini(
        model="gemini-2.0-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=SYSTEM_INSTRUCTION,
    tools=[
        # AST analysis tools — pure Python, run in-process
        parse_python_file,
        extract_code_issues,

        # MCP FileSystem server — spawned automatically by ADK via stdio.
        # Scoped to WORKSPACE_ROOT; only read-only tools are exposed.
        # Requires Node.js / npx on PATH (installed during setup).
        MCPToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command="npx",
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

# ---------------------------------------------------------------------------
# ADK App
# The name MUST match this directory name ("app"). Do not change it.
# ---------------------------------------------------------------------------

app = App(
    root_agent=root_agent,
    name="app",
)
