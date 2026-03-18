import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { execFile } from 'child_process';

// ─── Globals ──────────────────────────────────────────────────────────────────
const EXTENSION_NAME = 'Ansible Code Reviewer';
const DIAG_SOURCE     = 'Ansible Reviewer';
let diagnosticCollection: vscode.DiagnosticCollection;
let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;

// ─── Severity mapping ─────────────────────────────────────────────────────────
function jsonSevToVscode(sev: string): vscode.DiagnosticSeverity {
  switch (sev.toLowerCase()) {
    case 'critical':
    case 'error':   return vscode.DiagnosticSeverity.Error;
    case 'warning': return vscode.DiagnosticSeverity.Warning;
    default:        return vscode.DiagnosticSeverity.Information;
  }
}

// ─── Locate reviewer.py ───────────────────────────────────────────────────────
function findReviewerScript(): string | null {
  const cfg = vscode.workspace.getConfiguration('ansibleReviewer');
  const explicit = cfg.get<string>('reviewerScriptPath', '').trim();
  if (explicit && fs.existsSync(explicit)) return explicit;

  const folders = vscode.workspace.workspaceFolders;
  if (!folders) return null;

  for (const folder of folders) {
    const candidate = path.join(folder.uri.fsPath, 'reviewer.py');
    if (fs.existsSync(candidate)) return candidate;
    // also check one level up (for cases where the project is a subfolder)
    const up = path.join(folder.uri.fsPath, '..', 'reviewer.py');
    if (fs.existsSync(up)) return path.resolve(up);
  }
  return null;
}

// ─── Run reviewer.py and return JSON results ──────────────────────────────────
function runReviewer(target: string): Promise<any[]> {
  return new Promise((resolve, reject) => {
    const cfg        = vscode.workspace.getConfiguration('ansibleReviewer');
    const python     = cfg.get<string>('pythonPath', 'python3');
    const minSev     = cfg.get<string>('minSeverity', 'info');
    const generateHtml = cfg.get<boolean>('generateHtmlReport', true);
    const rulesDir   = cfg.get<string>('rulesDir', '').trim();
    const script     = findReviewerScript();

    if (!script) {
      reject(new Error(
        'reviewer.py not found. Set "ansibleReviewer.reviewerScriptPath" in settings, ' +
        'or place reviewer.py in your workspace root.'
      ));
      return;
    }

    const args = [script, target, '--json', '--min-severity', minSev];
    if (rulesDir) args.push('--rules-dir', rulesDir);

    // Also kick off HTML generation in the background if enabled
    if (generateHtml) {
      const htmlArgs = [script, target, '--min-severity', minSev];
      if (rulesDir) htmlArgs.push('--rules-dir', rulesDir);
      execFile(python, htmlArgs, { cwd: path.dirname(script) }, () => {});
    }

    outputChannel.appendLine(`[${new Date().toLocaleTimeString()}] Running: ${python} ${args.join(' ')}`);

    execFile(python, args, { cwd: path.dirname(script), maxBuffer: 10 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (stderr) outputChannel.appendLine(`STDERR: ${stderr}`);
        try {
          const results = JSON.parse(stdout || '[]');
          resolve(results);
        } catch (parseErr) {
          outputChannel.appendLine(`Parse error: ${parseErr}`);
          outputChannel.appendLine(`stdout was: ${stdout.slice(0, 500)}`);
          // Non-zero exit with no JSON means a Python error
          reject(new Error(stderr || 'reviewer.py failed — check Output panel (Ansible Reviewer)'));
        }
      }
    );
  });
}

// ─── Apply diagnostics to Problems panel ──────────────────────────────────────
function applyDiagnostics(results: any[]) {
  diagnosticCollection.clear();
  const diagMap = new Map<string, vscode.Diagnostic[]>();
  let total = 0;

  for (const fileResult of results) {
    const filePath = fileResult.file;
    const diags: vscode.Diagnostic[] = [];

    for (const v of fileResult.violations) {
      const lineNum = Math.max(0, (v.line || 1) - 1);  // VS Code is 0-indexed
      const range = new vscode.Range(lineNum, 0, lineNum, 999);

      const diag = new vscode.Diagnostic(
        range,
        `[${v.rule_id}] ${v.rule_name}: ${v.description}`,
        jsonSevToVscode(v.severity)
      );
      diag.source = DIAG_SOURCE;
      diag.code   = {
        value: v.rule_id,
        target: vscode.Uri.parse(`https://github.com/search?q=${v.rule_id}`)
      };

      // Add rationale as related info
      if (v.rationale) {
        diag.relatedInformation = [
          new vscode.DiagnosticRelatedInformation(
            new vscode.Location(vscode.Uri.file(filePath), range),
            `💡 Why: ${v.rationale}`
          )
        ];
      }

      diags.push(diag);
      total++;
    }

    if (diags.length > 0) {
      diagMap.set(filePath, diags);
    }
  }

  for (const [filePath, diags] of diagMap) {
    diagnosticCollection.set(vscode.Uri.file(filePath), diags);
  }

  return total;
}

// ─── Status bar helpers ───────────────────────────────────────────────────────
function setStatus(text: string, tooltip?: string, spinning = false) {
  const cfg = vscode.workspace.getConfiguration('ansibleReviewer');
  if (!cfg.get<boolean>('showStatusBarItem', true)) return;
  statusBarItem.text = spinning ? `$(sync~spin) ${text}` : `$(shield) ${text}`;
  statusBarItem.tooltip = tooltip || text;
  statusBarItem.show();
}

// ─── Core review flow ─────────────────────────────────────────────────────────
async function reviewTarget(target: string, label: string) {
  setStatus(`Reviewing ${label}…`, undefined, true);
  outputChannel.appendLine(`\n──── Review: ${label} ────`);

  try {
    const results = await runReviewer(target);
    const total   = applyDiagnostics(results);

    const critErrors = results.reduce((acc, r) =>
      acc + r.violations.filter((v: any) =>
        v.severity === 'critical' || v.severity === 'error').length, 0);

    if (total === 0) {
      setStatus('Ansible: ✓ Clean', 'No violations found');
      vscode.window.showInformationMessage(`✅ Ansible Reviewer: No violations found in ${label}`);
    } else if (critErrors > 0) {
      setStatus(`Ansible: ${total} issues (${critErrors} critical/error)`, 'Click to open Problems panel');
      vscode.window.showErrorMessage(
        `🔴 Ansible Reviewer: ${total} issues found (${critErrors} critical/error) in ${label}`,
        'Open Problems'
      ).then(sel => { if (sel) vscode.commands.executeCommand('workbench.panel.markers.view.focus'); });
    } else {
      setStatus(`Ansible: ${total} issues`, 'Click to open Problems panel');
      vscode.window.showWarningMessage(
        `🟡 Ansible Reviewer: ${total} warnings/info in ${label}`,
        'Open Problems'
      ).then(sel => { if (sel) vscode.commands.executeCommand('workbench.panel.markers.view.focus'); });
    }

    outputChannel.appendLine(`Result: ${total} total violations`);
  } catch (err: any) {
    setStatus('Ansible: Error', err.message);
    outputChannel.appendLine(`ERROR: ${err.message}`);
    vscode.window.showErrorMessage(`Ansible Reviewer Error: ${err.message}`, 'Show Output')
      .then(sel => { if (sel) outputChannel.show(); });
  }
}

// ─── Open latest HTML report ──────────────────────────────────────────────────
async function openLatestReport() {
  const script = findReviewerScript();
  if (!script) {
    vscode.window.showErrorMessage('reviewer.py not found — cannot locate reports directory.');
    return;
  }
  const reportsDir = path.join(path.dirname(script), 'reports');
  if (!fs.existsSync(reportsDir)) {
    vscode.window.showWarningMessage('No reports directory found. Run a review first.');
    return;
  }
  const files = fs.readdirSync(reportsDir)
    .filter(f => f.endsWith('.html'))
    .map(f => ({ f, t: fs.statSync(path.join(reportsDir, f)).mtime.getTime() }))
    .sort((a, b) => b.t - a.t);

  if (!files.length) {
    vscode.window.showWarningMessage('No HTML reports found. Run a review first.');
    return;
  }
  const reportPath = vscode.Uri.file(path.join(reportsDir, files[0].f));
  vscode.env.openExternal(reportPath);
}

// ─── List all rules in Output panel ──────────────────────────────────────────
async function listRules() {
  const cfg    = vscode.workspace.getConfiguration('ansibleReviewer');
  const python = cfg.get<string>('pythonPath', 'python3');
  const script = findReviewerScript();
  if (!script) {
    vscode.window.showErrorMessage('reviewer.py not found.');
    return;
  }
  outputChannel.show();
  outputChannel.appendLine('\n──── Loaded Rules ────');
  execFile(python, [script, '--list-rules'], { cwd: path.dirname(script) },
    (err, stdout, stderr) => {
      outputChannel.appendLine(stdout || stderr || 'No output');
    }
  );
}

// ─── Extension activate ───────────────────────────────────────────────────────
export function activate(context: vscode.ExtensionContext) {
  // Diagnostic collection (feeds the Problems panel)
  diagnosticCollection = vscode.languages.createDiagnosticCollection('ansible-reviewer');
  context.subscriptions.push(diagnosticCollection);

  // Output channel
  outputChannel = vscode.window.createOutputChannel(EXTENSION_NAME);
  context.subscriptions.push(outputChannel);

  // Status bar
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBarItem.command = 'ansibleReviewer.reviewFile';
  statusBarItem.text = '$(shield) Ansible Reviewer';
  statusBarItem.tooltip = 'Click to review current file';
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // ── Commands ──
  context.subscriptions.push(
    vscode.commands.registerCommand('ansibleReviewer.reviewFile', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) { vscode.window.showWarningMessage('No file open.'); return; }
      await reviewTarget(editor.document.uri.fsPath, path.basename(editor.document.uri.fsPath));
    }),

    vscode.commands.registerCommand('ansibleReviewer.reviewWorkspace', async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders) { vscode.window.showWarningMessage('No workspace open.'); return; }
      await reviewTarget(folders[0].uri.fsPath, 'Workspace');
    }),

    vscode.commands.registerCommand('ansibleReviewer.openReport', openLatestReport),

    vscode.commands.registerCommand('ansibleReviewer.listRules', listRules),

    vscode.commands.registerCommand('ansibleReviewer.clearDiagnostics', () => {
      diagnosticCollection.clear();
      setStatus('Ansible Reviewer');
      outputChannel.appendLine('Diagnostics cleared.');
    })
  );

  // ── Run on save ──
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      const cfg = vscode.workspace.getConfiguration('ansibleReviewer');
      if (!cfg.get<boolean>('runOnSave', true)) return;
      const ext = path.extname(doc.uri.fsPath).toLowerCase();
      if (ext !== '.yml' && ext !== '.yaml') return;
      await reviewTarget(doc.uri.fsPath, path.basename(doc.uri.fsPath));
    })
  );

  outputChannel.appendLine(`${EXTENSION_NAME} activated. reviewer.py: ${findReviewerScript() || 'not found yet'}`);
}

export function deactivate() {
  diagnosticCollection?.dispose();
  statusBarItem?.dispose();
  outputChannel?.dispose();
}
