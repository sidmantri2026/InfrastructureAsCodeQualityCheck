import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { execFile } from 'child_process';

const EXTENSION_NAME = 'IaC Quality Reviewer';
const DIAG_SOURCE    = 'IaC Reviewer';

const SUPPORTED_EXTENSIONS = new Set(['.yml','.yaml','.sh','.ps1','.groovy','.jenkinsfile']);
const SUPPORTED_EXACT_NAMES = new Set(['jenkinsfile']);

let diagnosticCollection: vscode.DiagnosticCollection;
let statusBarItem: vscode.StatusBarItem;
let outputChannel: vscode.OutputChannel;

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

function runReviewer(target: string, generateHtml: boolean): Promise<any[]> {
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

    const jsonArgs = [script, target, '--json', '--min-severity', minSev];
    if (rulesDir) { jsonArgs.push('--rules-dir', rulesDir); }

    if (generateHtml) {
      const htmlArgs = [script, target, '--min-severity', minSev];
      if (rulesDir) { htmlArgs.push('--rules-dir', rulesDir); }
      execFile(python, htmlArgs, { cwd: path.dirname(script) }, () => {
        outputChannel.appendLine(`[HTML] Report saved to ${getReportsDir(script)}`);
      });
    }

    outputChannel.appendLine(`[${new Date().toLocaleTimeString()}] ${python} reviewer.py ${target}`);

    execFile(python, jsonArgs, { cwd: path.dirname(script), maxBuffer: 20 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (stderr) { outputChannel.appendLine(`STDERR: ${stderr}`); }
        try { resolve(JSON.parse(stdout || '[]')); }
        catch { reject(new Error(stderr || 'reviewer.py produced no JSON — see Output panel')); }
      }
    );
  });
}

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
          new vscode.Location(vscode.Uri.file(fileResult.file), range), `💡 Why: ${v.rationale}`)];
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

function setStatus(text: string, tooltip?: string, spinning = false) {
  if (!vscode.workspace.getConfiguration('iacReviewer').get<boolean>('showStatusBarItem', true)) { return; }
  statusBarItem.text    = spinning ? `$(sync~spin) ${text}` : `$(shield) ${text}`;
  statusBarItem.tooltip = tooltip || text;
  statusBarItem.show();
}

async function reviewTarget(target: string, label: string, forceHtml = false) {
  const cfg         = vscode.workspace.getConfiguration('iacReviewer');
  const generateHtml = forceHtml || cfg.get<boolean>('generateHtmlReport', true);
  setStatus(`Reviewing ${label}…`, undefined, true);
  outputChannel.appendLine(`\n──── Reviewing: ${label} ────`);
  try {
    const results    = await runReviewer(target, generateHtml);
    const total      = applyDiagnostics(results);
    const critErrors = results.reduce((acc, r) =>
      acc + r.violations.filter((v: any) => v.severity === 'critical' || v.severity === 'error').length, 0);
    if (total === 0) {
      setStatus('IaC: ✓ Clean');
      vscode.window.showInformationMessage(`✅ IaC Reviewer: No violations in ${label}`);
    } else if (critErrors > 0) {
      setStatus(`IaC: ${total} issues (${critErrors} critical/error)`);
      vscode.window.showErrorMessage(
        `🔴 IaC Reviewer: ${total} issues (${critErrors} critical/error) in ${label}`,
        'Open Problems', 'Open HTML Report'
      ).then(sel => {
        if (sel === 'Open Problems')    { vscode.commands.executeCommand('workbench.panel.markers.view.focus'); }
        if (sel === 'Open HTML Report') { openLatestReport(); }
      });
    } else {
      setStatus(`IaC: ${total} issues`);
      vscode.window.showWarningMessage(
        `🟡 IaC Reviewer: ${total} warnings/info in ${label}`,
        'Open Problems', 'Open HTML Report'
      ).then(sel => {
        if (sel === 'Open Problems')    { vscode.commands.executeCommand('workbench.panel.markers.view.focus'); }
        if (sel === 'Open HTML Report') { openLatestReport(); }
      });
    }
  } catch (err: any) {
    setStatus('IaC: Error');
    outputChannel.appendLine(`ERROR: ${err.message}`);
    vscode.window.showErrorMessage(`IaC Reviewer Error: ${err.message}`, 'Show Output')
      .then(sel => { if (sel) { outputChannel.show(); } });
  }
}

async function openLatestReport() {
  const script = findReviewerScript();
  if (!script) { vscode.window.showErrorMessage('reviewer.py not found.'); return; }
  const reportsDir = getReportsDir(script);
  if (!fs.existsSync(reportsDir)) { vscode.window.showWarningMessage('No reports found. Run a review first.'); return; }
  const files = fs.readdirSync(reportsDir)
    .filter(f => f.endsWith('.html'))
    .map(f => ({ f, t: fs.statSync(path.join(reportsDir, f)).mtime.getTime() }))
    .sort((a, b) => b.t - a.t);
  if (!files.length) { vscode.window.showWarningMessage('No HTML reports found.'); return; }
  vscode.env.openExternal(vscode.Uri.file(path.join(reportsDir, files[0].f)));
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

function resolveExplorerTarget(uri?: vscode.Uri, uris?: vscode.Uri[]): { target: string; label: string } | null {
  if (uris && uris.length > 1) {
    const dirs = uris.map(u => fs.statSync(u.fsPath).isDirectory() ? u.fsPath : path.dirname(u.fsPath));
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
          `IaC Reviewer: ${path.basename(uri.fsPath)} is not a supported file type.\n` +
          'Supported: .yml .yaml .sh .ps1 .groovy Jenkinsfile');
        return null;
      }
      return { target: uri.fsPath, label: path.basename(uri.fsPath) };
    } catch { return null; }
  }
  return null;
}

export function activate(context: vscode.ExtensionContext) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection('iac-reviewer');
  outputChannel        = vscode.window.createOutputChannel(EXTENSION_NAME);
  statusBarItem        = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBarItem.command = 'iacReviewer.reviewFile';
  statusBarItem.text    = '$(shield) IaC Reviewer';
  statusBarItem.tooltip = 'Click to review current file';
  statusBarItem.show();
  context.subscriptions.push(diagnosticCollection, outputChannel, statusBarItem);

  context.subscriptions.push(
    vscode.commands.registerCommand('iacReviewer.reviewFile', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) { vscode.window.showWarningMessage('No file is currently open.'); return; }
      await reviewTarget(editor.document.uri.fsPath, path.basename(editor.document.uri.fsPath));
    }),

    // Right-click on file or folder → Review & Diagnostics
    vscode.commands.registerCommand('iacReviewer.reviewSelected',
      async (uri?: vscode.Uri, uris?: vscode.Uri[]) => {
        const r = resolveExplorerTarget(uri, uris);
        if (r) { await reviewTarget(r.target, r.label); }
      }),

    // Right-click on file or folder → Review & Open HTML Report
    vscode.commands.registerCommand('iacReviewer.reviewSelectedHtml',
      async (uri?: vscode.Uri, uris?: vscode.Uri[]) => {
        const r = resolveExplorerTarget(uri, uris);
        if (!r) { return; }
        await reviewTarget(r.target, r.label, true);
        setTimeout(openLatestReport, 3000);
      }),

    vscode.commands.registerCommand('iacReviewer.reviewWorkspace', async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders) { vscode.window.showWarningMessage('No workspace open.'); return; }
      await reviewTarget(folders[0].uri.fsPath, 'Workspace');
    }),

    vscode.commands.registerCommand('iacReviewer.openReport', openLatestReport),
    vscode.commands.registerCommand('iacReviewer.listRules',  listRules),

    vscode.commands.registerCommand('iacReviewer.clearDiagnostics', () => {
      diagnosticCollection.clear();
      setStatus('IaC Reviewer');
      outputChannel.appendLine('Diagnostics cleared.');
    }),

    // On save — covers ALL supported file types
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      if (!vscode.workspace.getConfiguration('iacReviewer').get<boolean>('runOnSave', true)) { return; }
      if (!isSupportedFile(doc.uri.fsPath)) { return; }
      await reviewTarget(doc.uri.fsPath, path.basename(doc.uri.fsPath));
    })
  );

  const script = findReviewerScript();
  outputChannel.appendLine(
    `${EXTENSION_NAME} v1.1 activated. reviewer.py: ${script || '⚠ NOT FOUND'}`
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
