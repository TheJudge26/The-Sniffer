/**
 * extension.ts — LocalCode Optimizer VS Code Extension
 *
 * Dual-Engine Review Architecture:
 *
 *   PRIMARY  → ADK 2.0 agent (Gemini via uv run adk run app)
 *              Spawned as a child process; stdout/stderr streamed live.
 *
 *   FALLBACK → Ollama local model (qwen2.5-coder:0.5b, port 11434)
 *              Triggered automatically when ADK exits with a non-zero
 *              code (e.g. 429 quota error, network failure, auth issue).
 *              Reads the file, builds a review prompt, and streams the
 *              NDJSON response token-by-token into the same Output Channel.
 *
 * The user sees a seamless, uninterrupted experience in both cases.
 */

import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';
import * as os from 'os';

// ---------------------------------------------------------------------------
// Output channel — created once, reused across all reviews
// ---------------------------------------------------------------------------
let outputChannel: vscode.OutputChannel;

// Track in-flight review processes so we can kill on deactivate
const activeProcesses = new Set<ChildProcess>();

// ---------------------------------------------------------------------------
// Activate
// ---------------------------------------------------------------------------
export function activate(context: vscode.ExtensionContext): void {
  // Create the output channel — visible in the "Output" panel dropdown
  outputChannel = vscode.window.createOutputChannel('LocalCode Optimizer');

  outputChannel.appendLine('LocalCode Optimizer activated.');
  outputChannel.appendLine(
    'Save any Python file to trigger an automated code review.'
  );

  const saveListener = vscode.workspace.onDidSaveTextDocument(
    (document: vscode.TextDocument) => {
      if (document.languageId !== 'python') {
        return;
      }
      triggerReview(document.uri.fsPath);
    }
  );

  const reviewCommand = vscode.commands.registerCommand(
    'localcode-optimizer.reviewFile',
    () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage(
          'LocalCode Optimizer: No active editor found.'
        );
        return;
      }
      if (editor.document.languageId !== 'python') {
        vscode.window.showWarningMessage(
          'LocalCode Optimizer: Active file is not a Python file.'
        );
        return;
      }
      triggerReview(editor.document.uri.fsPath);
    }
  );

  context.subscriptions.push(saveListener, reviewCommand, outputChannel);
}

function triggerReview(absoluteFilePath: string): void {
  const config = vscode.workspace.getConfiguration('localcode-optimizer');

  const agentWorkingDir: string = config.get(
    'agentWorkingDir',
    'c:\\Users\\dodo7\\OneDrive\\Desktop\\the sniffer\\localcode-optimizer'
  );
  const uvPath: string = config.get('uvPath', 'uv');
  const primaryEngine: string = config.get('primaryEngine', 'Gemini');
  const ollamaModelName: string = config.get('ollamaModelName', 'qwen2.5-coder:0.5b');

  const rel = path.relative(agentWorkingDir, absoluteFilePath);
  const filePathForPrompt =
    rel.startsWith('..') || path.isAbsolute(rel)
      ? absoluteFilePath
      : rel;

  const prompt = `Review the file ${filePathForPrompt}`;

  outputChannel.show(/* preserveFocus */ true);
  outputChannel.appendLine('');
  outputChannel.appendLine('─'.repeat(70));
  outputChannel.appendLine(
    `▶  LocalCode Optimizer  |  ${new Date().toLocaleTimeString()}`
  );
  outputChannel.appendLine(`   File   : ${filePathForPrompt}`);
  outputChannel.appendLine(`   Dir    : ${agentWorkingDir}`);
  outputChannel.appendLine(`   Engine : ${primaryEngine}${primaryEngine === 'Ollama' ? ` (${ollamaModelName})` : ''}`);
  outputChannel.appendLine('─'.repeat(70));

  // If the user has selected Ollama as the primary engine, bypass ADK entirely.
  if (primaryEngine === 'Ollama') {
    runOllamaFallback(absoluteFilePath, ollamaModelName);
    return;
  }

  const childEnv: NodeJS.ProcessEnv = {
    ...process.env,
    GEMINI_API_KEY:
      process.env.GEMINI_API_KEY ?? '',
    GOOGLE_GENAI_USE_VERTEXAI: 'False',
    PATH: buildPath(),
  };

  // shell: true → resolves uv on Windows PATH; prompt is double-quoted to
  // prevent the shell from splitting on spaces (extra positional args error).
  const quotedPrompt = `"${prompt}"`;

  const child = spawn(
    uvPath,
    ['run', 'adk', 'run', 'app', quotedPrompt],
    {
      cwd: agentWorkingDir,
      env: childEnv,
      shell: true,
      windowsHide: true,
    }
  );

  activeProcesses.add(child);

  child.stdout?.on('data', (data: Buffer) => {
    outputChannel.append(data.toString());
  });

  child.stderr?.on('data', (data: Buffer) => {
    const text = data.toString();
    // Filter out noisy ADK internals; only show meaningful lines
    if (shouldShowStderrLine(text)) {
      outputChannel.append(text);
    }
  });

  // ── Process error (e.g. uv not found) ──────────────────────────────────
  child.on('error', (err: Error) => {
    outputChannel.appendLine('');
    outputChannel.appendLine(`[ERROR] Could not start the ADK agent.`);
    outputChannel.appendLine(`        ${err.message}`);
    outputChannel.appendLine('');
    outputChannel.appendLine(
      '  Tip: Make sure "uv" is on your PATH or set "localcode-optimizer.uvPath"'
    );
    outputChannel.appendLine(
      `       in VS Code settings to the full path of the uv executable.`
    );
    vscode.window.showErrorMessage(
      `LocalCode Optimizer: Failed to start agent — ${err.message}`
    );
    activeProcesses.delete(child);
  });

  child.on('close', (code: number | null) => {
    activeProcesses.delete(child);

    if (code === 0) {
      outputChannel.appendLine('');
      outputChannel.appendLine(`✓ Review complete (Gemini/ADK)  |  ${new Date().toLocaleTimeString()}`);
      outputChannel.appendLine('─'.repeat(70));
      return;
    }

    // ── Non-zero exit → engage offline fallback ────────────────────────
    outputChannel.appendLine('');
    outputChannel.appendLine(`⚠ ADK agent exited with code ${code ?? '?'} — activating offline fallback`);
    outputChannel.appendLine(`  → Engine : ${ollamaModelName}  (Ollama · localhost:11434)`);
    outputChannel.appendLine(`  → File   : ${absoluteFilePath}`);
    outputChannel.appendLine('─'.repeat(70));

    runOllamaFallback(absoluteFilePath, ollamaModelName);
  });
}

// ---------------------------------------------------------------------------
// Offline fallback — Ollama / qwen2.5-coder:0.5b
// ---------------------------------------------------------------------------

/**
 * Reads the Python source file, builds a structured review prompt, and
 * streams the Ollama NDJSON response token-by-token into the Output Channel.
 *
 * Ollama's /api/generate endpoint returns one JSON object per line:
 *   { "response": "<token>", "done": false }  ← intermediate chunks
 *   { "response": "", "done": true, "eval_count": N, ... }  ← final stats
 */
function runOllamaFallback(absoluteFilePath: string, modelName: string = 'qwen2.5-coder:0.5b'): void {
  let sourceCode: string;
  try {
    sourceCode = fs.readFileSync(absoluteFilePath, 'utf-8');
  } catch (err) {
    outputChannel.appendLine(`[FALLBACK ERROR] Could not read file: ${err}`);
    outputChannel.appendLine('─'.repeat(70));
    return;
  }

  // Truncate very large files to stay within the model's context window.
  // qwen2.5-coder:0.5b has a 32k token context; 500 lines ≈ safe upper bound.
  const lines = sourceCode.split('\n');
  const MAX_LINES = 500;
  const truncated = lines.length > MAX_LINES;
  const codeForPrompt = truncated
    ? lines.slice(0, MAX_LINES).join('\n') +
      `\n\n# ... [truncated: showing first ${MAX_LINES} of ${lines.length} lines]`
    : sourceCode;

  const ollamaPrompt =
    `You are an expert Python code reviewer. Analyse the file below and produce a structured review with four sections:\n` +
    `\n## Summary\nOne paragraph: what the code does and its overall quality.` +
    `\n\n## Detected Issues\nMarkdown table — columns: # | Rule | Severity | Line | Description` +
    `\nSeverity order: error > warning > info.` +
    `\n\n## Refactoring Suggestions\nFor each error/warning: why it matters + Before/After code blocks.` +
    `\n\n## Overall Quality Score\n1 (unreadable) to 10 (production-ready) with 2-3 sentence rationale.` +
    `\n\nFile: ${path.basename(absoluteFilePath)}` +
    (truncated ? `  (first ${MAX_LINES} lines shown)` : '') +
    `\n\n\`\`\`python\n${codeForPrompt}\n\`\`\`\n\nProvide your review:`;

  const requestBody = JSON.stringify({
    model: modelName,
    prompt: ollamaPrompt,
    stream: true,   // NDJSON streaming — one JSON object per line
  });

  const options: http.RequestOptions = {
    hostname: 'localhost',
    port: 11434,
    path: '/api/generate',
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(requestBody),
    },
  };

  const req = http.request(options, (res) => {
    if (res.statusCode !== 200) {
      outputChannel.appendLine(
        `[FALLBACK ERROR] Ollama returned HTTP ${res.statusCode}`
      );
      outputChannel.appendLine('─'.repeat(70));
      return;
    }

    // Ollama sends NDJSON — chunks may not align with line boundaries,
    // so we buffer and split on newlines manually.
    let buffer = '';

    res.on('data', (chunk: Buffer) => {
      buffer += chunk.toString('utf-8');

      // Extract all complete lines from the buffer
      const newlineIdx = buffer.lastIndexOf('\n');
      if (newlineIdx === -1) {
        return; // no complete line yet — wait for more data
      }

      const completeLines = buffer.slice(0, newlineIdx).split('\n');
      buffer = buffer.slice(newlineIdx + 1); // keep the incomplete tail

      for (const line of completeLines) {
        if (!line.trim()) {
          continue;
        }
        try {
          const obj = JSON.parse(line) as {
            response?: string;
            done?: boolean;
            model?: string;
            eval_count?: number;
            eval_duration?: number;
          };

          if (obj.response) {
            outputChannel.append(obj.response);
          }

          // Final chunk — print performance stats
          if (obj.done === true) {
            const tokensPerSec =
              obj.eval_count && obj.eval_duration
                ? Math.round(obj.eval_count / (obj.eval_duration / 1e9))
                : null;
            outputChannel.appendLine('');
            outputChannel.appendLine('');
            outputChannel.appendLine(
              `✓ Offline review complete (${obj.model ?? modelName})` +
              `  |  ${new Date().toLocaleTimeString()}` +
              (tokensPerSec !== null ? `  |  ${tokensPerSec} tok/s` : '')
            );
            outputChannel.appendLine('─'.repeat(70));
          }
        } catch {
          // Skip any malformed JSON lines (shouldn't happen with Ollama)
        }
      }
    });

    res.on('error', (err: Error) => {
      outputChannel.appendLine(`[FALLBACK ERROR] Stream error: ${err.message}`);
      outputChannel.appendLine('─'.repeat(70));
    });
  });

  // ── Handle connection errors (Ollama not running, port closed, etc.) ──
  req.on('error', (err: Error) => {
    outputChannel.appendLine('');
    outputChannel.appendLine(`[FALLBACK ERROR] Cannot reach Ollama: ${err.message}`);
    outputChannel.appendLine('  Tip: start the server with  ollama serve');
    outputChannel.appendLine(
      `  Tip: confirm model is pulled with  ollama list | findstr qwen2.5-coder`
    );
    outputChannel.appendLine('─'.repeat(70));
  });

  req.write(requestBody);
  req.end();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a PATH string that guarantees uv and Node.js are findable
 * on Windows regardless of how VS Code was launched.
 */
function buildPath(): string {
  const extraDirs = [
    `${os.homedir()}\\.local\\bin`,     // uv installs here
    'C:\\Program Files\\nodejs',         // Node.js / npx
    'C:\\Program Files\\Git\\cmd',       // git (useful for future features)
  ];
  const existing = process.env.PATH ?? '';
  return [...extraDirs, existing].join(path.delimiter);
}

/**
 * Filter out routine ADK/grpc/asyncio noise from stderr so the output
 * channel stays readable. Returns true for lines worth showing.
 */
function shouldShowStderrLine(line: string): boolean {
  const noisePatterns = [
    /^WARNING:root:/,
    /^INFO:httpx:/,
    /^DEBUG:/,
    /grpc\._channel/,
    /asyncio/,
    /^\s*$/,  // blank lines
  ];
  return !noisePatterns.some((rx) => rx.test(line));
}

export function deactivate(): void {
  for (const child of activeProcesses) {
    child.kill();
  }
  activeProcesses.clear();
}
