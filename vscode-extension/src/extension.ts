import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import { execFile, spawn } from 'child_process';

// ─── Constants ────────────────────────────────────────────────────────────────
const EXTENSION_NAME = 'IaC Quality Reviewer';
const DIAG_SOURCE    = 'IaC Reviewer';
const SUPPORTED_EXTENSIONS  = new Set(['.yml','.yaml','.sh','.ps1','.groovy','.jenkinsfile']);
const SUPPORTED_EXACT_NAMES = new Set(['jenkinsfile']);

let diagnosticCollection: vscode.DiagnosticCollection;
let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;

// ─── Helpers ──────────────────────────────────────────────────────────────────
function isSupportedFile(filePath: string): boolean {
  const name = path.basename(filePath).toLowerCase();
  const ext  = path.extname(filePath).toLowerCase();
  return SUPPORTED_EXTENSIONS.has(ext) || SUPPORTED_EXACT_NAMES.has(name);
}

function jsonSevToVscode(sev: string): vscode.DiagnosticSeverity {
  switch (sev.toLowerCase()) {
    case 'critical':
    case 'error':   return vscode.DiagnosticSeverity.Error;
    case 'warning': return vscode.DiagnosticSeverity.Warning;
    default:        return vscode.DiagnosticSeverity.Information;
  }
}

// ─── Open in system browser (bypasses VS Code WebView) ───────────────────────
function openInSystemBrowser(filePath: string): void {
  // Copy the report to a temp file — some OS open handlers prefer temp paths
  // and it avoids any VS Code internal URI interception.
  const tmpPath = path.join(os.tmpdir(), `iac-review-${Date.now()}.html`);
  try {
    fs.copyFileSync(filePath, tmpPath);
  } catch {
    // fallback to original path if copy fails
  }
  const target = fs.existsSync(tmpPath) ? tmpPath : filePath;

  outputChannel.appendLine(`[HTML] Opening in system browser: ${target}`);

  // Use OS-specific command to guarantee system browser, not VS Code WebView
  if (process.platform === 'darwin') {
    spawn('open', [target], { detached: true, stdio: 'ignore' }).unref();
  } else if (process.platform === 'win32') {
    // On Windows: 'start' is a cmd built-in, must use cmd.exe
    spawn('cmd.exe', ['/c', 'start', '', target], { detached: true, stdio: 'ignore' }).unref();
  } else {
    // Linux / other Unix
    spawn('xdg-open', [target], { detached: true, stdio: 'ignore' }).unref();
  }
}

// ─── Locate reviewer.py ───────────────────────────────────────────────────────
function findReviewerScript(): string | null {
  const cfg      = vscode.workspace.getConfiguration('iacReviewer');
  const explicit = cfg.get<string>('reviewerScriptPath', '').trim();
  if (explicit && fs.existsSync(explicit)) { return explicit; }
  const folders = vscode.workspace.workspaceFolders;
  if (!folders) { return null; }
  for (const folder of folders) {
    const candidate = path.join(folder.uri.fsPath, 'reviewer.py');
    if (fs.existsSync(candidate)) { return candidate; }
    const up = path.join(folder.uri.fsPath, '..', 'reviewer.py');
    if (fs.existsSync(up)) { return path.resolve(up); }
  }
  return null;
}

function getReportsDir(script: string): string {
  return path.join(path.dirname(script), 'reports');
}

// ─── Run reviewer.py ──────────────────────────────────────────────────────────
function runReviewer(target: string, generateHtml: boolean): Promise<{ results: any[]; reportPath: string | null }> {
  return new Promise((resolve, reject) => {
    const cfg      = vscode.workspace.getConfiguration('iacReviewer');
    const python   = cfg.get<string>('pythonPath', 'python3');
    const minSev   = cfg.get<string>('minSeverity', 'info');
    const rulesDir = cfg.get<string>('rulesDir', '').trim();
    const script   = findReviewerScript();

    if (!script) {
      reject(new Error(
        'reviewer.py not found.\n\nPlease either:\n' +
        '1. Open the InfrastructureAsCodeQualityCheck repository folder in VS Code, OR\n' +
        '2. Set "iacReviewer.reviewerScriptPath" in Settings to the full path of reviewer.py'
      ));
      return;
    }

    // Generate HTML first (synchronously waits), then parse JSON
    const ts        = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
    const reportDir = getReportsDir(script);
    if (!fs.existsSync(reportDir)) { fs.mkdirSync(reportDir, { recursive: true }); }
    const reportPath = path.join(reportDir, `review_${ts}.html`);

    // Step 1: Generate HTML report (blocks until done)
    const runHtmlAndJson = () => {
      const htmlArgs = [script, target, '--min-severity', minSev];
      if (rulesDir) { htmlArgs.push('--rules-dir', rulesDir); }
      htmlArgs.push('--output', reportPath);

      outputChannel.appendLine(`[${new Date().toLocaleTimeString()}] Generating report: ${reportPath}`);

      execFile(python, htmlArgs, { cwd: path.dirname(script), maxBuffer: 20 * 1024 * 1024 },
        (htmlErr, htmlOut, htmlStderr) => {
          if (htmlStderr) { outputChannel.appendLine(`[HTML stderr] ${htmlStderr}`); }
          const reportExists = fs.existsSync(reportPath);
          outputChannel.appendLine(`[HTML] Report written: ${reportExists}`);

          // Step 2: Get JSON results for diagnostics
          const jsonArgs = [script, target, '--json', '--min-severity', minSev];
          if (rulesDir) { jsonArgs.push('--rules-dir', rulesDir); }

          execFile(python, jsonArgs, { cwd: path.dirname(script), maxBuffer: 20 * 1024 * 1024 },
            (err, stdout, stderr) => {
              if (stderr) { outputChannel.appendLine(`[JSON stderr] ${stderr}`); }
              try {
                const results = JSON.parse(stdout || '[]');
                resolve({ results, reportPath: reportExists ? reportPath : null });
              } catch {
                // JSON failed but HTML may have worked
                resolve({ results: [], reportPath: reportExists ? reportPath : null });
              }
            }
          );
        }
      );
    };

    runHtmlAndJson();
  });
}

// ─── Apply diagnostics ────────────────────────────────────────────────────────
function applyDiagnostics(results: any[]): number {
  diagnosticCollection.clear();
  const diagMap = new Map<string, vscode.Diagnostic[]>();
  let total = 0;
  for (const fileResult of results) {
    const diags: vscode.Diagnostic[] = [];
    for (const v of fileResult.violations) {
      const lineNum = Math.max(0, (v.line || 1) - 1);
      const range   = new vscode.Range(lineNum, 0, lineNum, 999);
      const diag    = new vscode.Diagnostic(range,
        `[${v.rule_id}] ${v.rule_name}: ${v.description}`,
        jsonSevToVscode(v.severity));
      diag.source = DIAG_SOURCE;
      diag.code   = { value: v.rule_id,
        target: vscode.Uri.parse('https://github.com/sidmantri2026/InfrastructureAsCodeQualityCheck') };
      if (v.rationale) {
        diag.relatedInformation = [new vscode.DiagnosticRelatedInformation(
          new vscode.Location(vscode.Uri.file(fileResult.file), range),
          `💡 Why: ${v.rationale}`)];
      }
      diags.push(diag);
      total++;
    }
    if (diags.length) { diagMap.set(fileResult.file, diags); }
  }
  for (const [fp, diags] of diagMap) {
    diagnosticCollection.set(vscode.Uri.file(fp), diags);
  }
  return total;
}

// ─── Status bar ───────────────────────────────────────────────────────────────
function setStatus(text: string, tooltip?: string, spinning = false) {
  if (!vscode.workspace.getConfiguration('iacReviewer').get<boolean>('showStatusBarItem', true)) { return; }
  statusBarItem.text    = spinning ? `$(sync~spin) ${text}` : `$(shield) ${text}`;
  statusBarItem.tooltip = tooltip || text;
  statusBarItem.show();
}

// ─── Core review flow ─────────────────────────────────────────────────────────
async function reviewTarget(target: string, label: string, openHtml = false) {
  const cfg          = vscode.workspace.getConfiguration('iacReviewer');
  const generateHtml = openHtml || cfg.get<boolean>('generateHtmlReport', true);

  setStatus(`Reviewing ${label}…`, undefined, true);
  outputChannel.appendLine(`\n──── Reviewing: ${label} ────`);

  try {
    const { results, reportPath } = await runReviewer(target, generateHtml);
    const total      = applyDiagnostics(results);
    const critErrors = results.reduce((acc, r) =>
      acc + r.violations.filter((v: any) => v.severity === 'critical' || v.severity === 'error').length, 0);

    // Open HTML in system browser if requested or configured
    if (openHtml && reportPath) {
      openInSystemBrowser(reportPath);
    }

    if (total === 0) {
      setStatus('IaC: ✓ Clean');
      vscode.window.showInformationMessage(`✅ IaC Reviewer: No violations in ${label}`);
    } else if (critErrors > 0) {
      setStatus(`IaC: ${total} issues (${critErrors} critical/error)`);
      const choice = await vscode.window.showErrorMessage(
        `🔴 IaC Reviewer: ${total} issues (${critErrors} critical/error) in ${label}`,
        'Open Problems', 'Open HTML Report'
      );
      if (choice === 'Open Problems')    { vscode.commands.executeCommand('workbench.panel.markers.view.focus'); }
      if (choice === 'Open HTML Report' && reportPath) { openInSystemBrowser(reportPath); }
    } else {
      setStatus(`IaC: ${total} issues`);
      const choice = await vscode.window.showWarningMessage(
        `🟡 IaC Reviewer: ${total} warnings/info in ${label}`,
        'Open Problems', 'Open HTML Report'
      );
      if (choice === 'Open Problems')    { vscode.commands.executeCommand('workbench.panel.markers.view.focus'); }
      if (choice === 'Open HTML Report' && reportPath) { openInSystemBrowser(reportPath); }
    }
  } catch (err: any) {
    setStatus('IaC: Error');
    outputChannel.appendLine(`ERROR: ${err.message}`);
    vscode.window.showErrorMessage(`IaC Reviewer Error: ${err.message}`, 'Show Output')
      .then(sel => { if (sel) { outputChannel.show(); } });
  }
}

// ─── Open latest HTML report in system browser ────────────────────────────────
async function openLatestReport() {
  const script = findReviewerScript();
  if (!script) { vscode.window.showErrorMessage('reviewer.py not found.'); return; }
  const reportsDir = getReportsDir(script);
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
  const reportPath = path.join(reportsDir, files[0].f);
  openInSystemBrowser(reportPath);
}

// ─── List rules ───────────────────────────────────────────────────────────────
async function openRuleManager() {
  const script = findReviewerScript();
  if (!script) { vscode.window.showErrorMessage('reviewer.py not found.'); return; }
  const managerPath = path.join(path.dirname(script), 'rule_manager.html');
  if (!require('fs').existsSync(managerPath)) {
    vscode.window.showErrorMessage(
      'rule_manager.html not found. Make sure you have the latest version of the repository.',
      'Open Repository'
    ).then(sel => {
      if (sel) { vscode.env.openExternal(vscode.Uri.parse('https://github.com/sidmantri2026/InfrastructureAsCodeQualityCheck')); }
    });
    return;
  }
  outputChannel.appendLine(`[Rule Manager] Opening: ${managerPath}`);
  openInSystemBrowser(managerPath);
}

async function listRules() {
  const cfg    = vscode.workspace.getConfiguration('iacReviewer');
  const python = cfg.get<string>('pythonPath', 'python3');
  const script = findReviewerScript();
  if (!script) { vscode.window.showErrorMessage('reviewer.py not found.'); return; }
  outputChannel.show();
  outputChannel.appendLine('\n──── Loaded Rules ────');
  execFile(python, [script, '--list-rules'], { cwd: path.dirname(script) },
    (err, stdout, stderr) => { outputChannel.appendLine(stdout || stderr || 'No output'); });
}

// ─── Resolve Explorer right-click target ──────────────────────────────────────
function resolveExplorerTarget(uri?: vscode.Uri, uris?: vscode.Uri[]): { target: string; label: string } | null {
  if (uris && uris.length > 1) {
    const dirs = uris.map(u => {
      try { return fs.statSync(u.fsPath).isDirectory() ? u.fsPath : path.dirname(u.fsPath); }
      catch { return path.dirname(u.fsPath); }
    });
    const common = dirs.reduce((a, b) => {
      let i = 0;
      while (i < a.length && i < b.length && a[i] === b[i]) { i++; }
      return a.slice(0, i);
    });
    return { target: common || dirs[0], label: `${uris.length} selected items` };
  }
  if (uri) {
    try {
      const stat = fs.statSync(uri.fsPath);
      if (stat.isDirectory()) {
        return { target: uri.fsPath, label: path.basename(uri.fsPath) + '/' };
      }
      if (!isSupportedFile(uri.fsPath)) {
        vscode.window.showWarningMessage(
          `IaC Reviewer: ${path.basename(uri.fsPath)} is not a supported file type. ` +
          'Supported: .yml .yaml .sh .ps1 .groovy Jenkinsfile');
        return null;
      }
      return { target: uri.fsPath, label: path.basename(uri.fsPath) };
    } catch { return null; }
  }
  return null;
}

// ─── Activate ─────────────────────────────────────────────────────────────────
export function activate(context: vscode.ExtensionContext) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection('iac-reviewer');
  outputChannel        = vscode.window.createOutputChannel(EXTENSION_NAME);
  statusBarItem        = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBarItem.command  = 'iacReviewer.reviewFile';
  statusBarItem.text     = '$(shield) IaC Reviewer';
  statusBarItem.tooltip  = 'Click to review current file';
  statusBarItem.show();
  context.subscriptions.push(diagnosticCollection, outputChannel, statusBarItem);

  context.subscriptions.push(

    // Review current open file (diagnostics only)
    vscode.commands.registerCommand('iacReviewer.reviewFile', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) { vscode.window.showWarningMessage('No file is currently open.'); return; }
      await reviewTarget(editor.document.uri.fsPath, path.basename(editor.document.uri.fsPath), false);
    }),

    // Explorer right-click → diagnostics only
    vscode.commands.registerCommand('iacReviewer.reviewSelected',
      async (uri?: vscode.Uri, uris?: vscode.Uri[]) => {
        const r = resolveExplorerTarget(uri, uris);
        if (r) { await reviewTarget(r.target, r.label, false); }
      }),

    // Explorer right-click → diagnostics + open HTML in system browser
    vscode.commands.registerCommand('iacReviewer.reviewSelectedHtml',
      async (uri?: vscode.Uri, uris?: vscode.Uri[]) => {
        const r = resolveExplorerTarget(uri, uris);
        if (r) { await reviewTarget(r.target, r.label, true); }
      }),

    // Review entire workspace
    vscode.commands.registerCommand('iacReviewer.reviewWorkspace', async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders) { vscode.window.showWarningMessage('No workspace open.'); return; }
      await reviewTarget(folders[0].uri.fsPath, 'Workspace', false);
    }),

    // Open latest report in system browser
    vscode.commands.registerCommand('iacReviewer.openReport',      openLatestReport),

    // Open Rule Manager GUI in system browser
    vscode.commands.registerCommand('iacReviewer.openRuleManager', openRuleManager),

    vscode.commands.registerCommand('iacReviewer.listRules',       listRules),

    vscode.commands.registerCommand('iacReviewer.clearDiagnostics', () => {
      diagnosticCollection.clear();
      setStatus('IaC Reviewer');
      outputChannel.appendLine('Diagnostics cleared.');
    }),

    // On save — covers ALL supported file types
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (!vscode.workspace.getConfiguration('iacReviewer').get<boolean>('runOnSave', true)) { return; }
      if (!isSupportedFile(doc.uri.fsPath)) { return; }
      await reviewTarget(doc.uri.fsPath, path.basename(doc.uri.fsPath), false);
    })
  );

  const script = findReviewerScript();
  outputChannel.appendLine(
    `${EXTENSION_NAME} v1.2 activated. reviewer.py: ${script || '⚠ NOT FOUND'}`
  );

  if (!script) {
    vscode.window.showWarningMessage(
      'IaC Reviewer: reviewer.py not found. Open the InfrastructureAsCodeQualityCheck repo folder, ' +
      'or configure iacReviewer.reviewerScriptPath in Settings.',
      'Open Settings'
    ).then(sel => {
      if (sel) { vscode.commands.executeCommand('workbench.action.openSettings', 'iacReviewer.reviewerScriptPath'); }
    });
  }
}

export function deactivate() {
  diagnosticCollection?.dispose();
  statusBarItem?.dispose();
  outputChannel?.dispose();
}
