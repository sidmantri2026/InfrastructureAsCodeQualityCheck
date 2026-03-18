# IaC Quality Reviewer

> Rule-driven static analysis for infrastructure automation code.  
> **Ansible · Bash · PowerShell · Jenkinsfile** — 211 rules, VS Code extension, HTML reports, Jenkins integration.

---

## ⚠ IMPORTANT — Read Before Installing

**Both the VS Code extension and the command-line tool require this repository to be cloned/downloaded to your local machine first.**

The VS Code extension is a thin wrapper. It calls `reviewer.py` from this repository. If `reviewer.py` is not present on your machine, the extension will not work.

---

## Step 1 — Get This Repository on Your Machine

### Option A — Clone from GitHub (recommended for your team)

```bash
git clone https://github.com/sidmantri2026/InfrastructureAsCodeQualityCheck.git
cd InfrastructureAsCodeQualityCheck
```

### Option B — Download as ZIP

1. Go to https://github.com/sidmantri2026/InfrastructureAsCodeQualityCheck
2. Click **Code → Download ZIP**
3. Extract to a **permanent location** (e.g. `~/repos/InfrastructureAsCodeQualityCheck`)
4. Do NOT use a temp folder or Downloads — the extension needs a stable path

---

## Step 2 — Install Python Dependency

```bash
# macOS / Linux
pip3 install pyyaml

# Windows
pip install pyyaml
```

Verify it works — run from inside the repo folder:

```bash
python3 reviewer.py --list-rules    # macOS / Linux
python  reviewer.py --list-rules    # Windows
```

You should see 211 rules listed. If so, the engine is ready.

---

## Step 3 — Install the VS Code Extension

The `.vsix` file is included in this repository.

1. Open **VS Code**
2. `Ctrl+Shift+P` → **Extensions: Install from VSIX...**
3. Navigate to the repo folder you cloned/extracted in Step 1
4. Open `vscode-extension/` → select **`iac-quality-reviewer-1.1.0.vsix`**
5. Click **Install**
6. **Reload VS Code** when prompted

After reload you will see `$(shield) IaC Reviewer` in the status bar at the bottom of VS Code.

---

## Step 4 — Open Your Project in VS Code

The extension auto-finds `reviewer.py` when the repo (or a project inside it) is open in VS Code.

**Recommended: open the repo folder itself as your workspace:**

```
File → Open Folder → InfrastructureAsCodeQualityCheck/
```

Or open your own Ansible/Bash/PowerShell project folder. The extension searches for `reviewer.py` in the workspace root and one level up.

**If auto-detect fails** (you see a "reviewer.py not found" warning):

1. `Ctrl+Shift+P` → **Open User Settings**
2. Search for `iacReviewer.reviewerScriptPath`
3. Set it to the full path of `reviewer.py`:
   - macOS/Linux: `/Users/yourname/repos/InfrastructureAsCodeQualityCheck/reviewer.py`
   - Windows: `C:\repos\InfrastructureAsCodeQualityCheck\reviewer.py`

---

## Using the Extension

### Auto-review on save

Save any `.yml`, `.yaml`, `.sh`, `.ps1`, `.groovy`, or `Jenkinsfile` and violations appear instantly as squiggles.

### Right-click in Explorer (NEW in v1.1)

Right-click any **file**, **folder**, or **multiple selected items**:

| Menu Option | What it does |
|---|---|
| 🔍 IaC Review: Review & Show Diagnostics | Squiggles in Problems panel |
| 📄 IaC Review: Review & Open HTML Report | Squiggles + opens HTML report in browser |

### Command Palette (`Ctrl+Shift+P` → type "IaC")

| Command | What it does |
|---|---|
| 🔍 Review Current File | Review the open file |
| 🔍 Review Entire Workspace | Review all supported files |
| 📂 Open Latest HTML Report | Open last HTML report in browser |
| 📋 List All Rules | Print all 211 rules to Output panel |
| 🧹 Clear All Diagnostics | Remove squiggles |

**Keyboard shortcut:** `Ctrl+Shift+A` (Mac: `Cmd+Shift+A`)

---

## Extension Settings

`File → Preferences → Settings → search "IaC Reviewer"`

| Setting | Default | Description |
|---|---|---|
| `iacReviewer.pythonPath` | `python3` | Set to `python` on Windows if needed |
| `iacReviewer.reviewerScriptPath` | auto | **Set manually if auto-detect fails** — full path to `reviewer.py` |
| `iacReviewer.rulesDir` | auto | Path to `rules/` directory |
| `iacReviewer.minSeverity` | `info` | Minimum severity shown in Problems panel |
| `iacReviewer.runOnSave` | `true` | Auto-review on file save |
| `iacReviewer.generateHtmlReport` | `true` | Also generate HTML on each review |
| `iacReviewer.showStatusBarItem` | `true` | Show status bar |

---

## Troubleshooting

**"reviewer.py not found"**
→ Open the repo folder in VS Code, OR set `iacReviewer.reviewerScriptPath` manually in Settings.

**"No module named yaml" / pyyaml error**
→ Run `pip3 install pyyaml` (or `pip install pyyaml` on Windows).

**Windows: "python3 not found"**
→ Change `iacReviewer.pythonPath` to `python` in Settings.

**Extension installed but no squiggles**
→ Open `View → Output → IaC Quality Reviewer` and check for error messages.

---

## Command Line Usage

```bash
python3 reviewer.py <file_or_dir>                          # Review file or directory
python3 reviewer.py <dir> --min-severity error             # Errors and criticals only
python3 reviewer.py <dir> --output my-report.html          # Custom report path
python3 reviewer.py <dir> --json                           # JSON output for CI
python3 reviewer.py --list-rules                           # List all rules
```

---

## Project Structure

```
InfrastructureAsCodeQualityCheck/
├── reviewer.py                               ← Core engine
├── requirements.txt
├── rules/                                    ← 211 rules across 22 files
│   ├── ansible_*.yaml                        ← 7 Ansible rule files (55 rules)
│   ├── bash_*.yaml                           ← 5 Bash rule files (54 rules)
│   ├── powershell_*.yaml                     ← 5 PowerShell rule files (53 rules)
│   └── jenkinsfile_*.yaml                    ← 4 Jenkinsfile rule files (40 rules + 8 hardcoding)
├── vscode-extension/
│   └── iac-quality-reviewer-1.1.0.vsix       ← ← Install this (Step 3)
├── sample_playbooks/                          ← Test files (yml, sh, ps1, Jenkinsfile)
├── rules_template.md / rules_template.docx   ← Write custom rules in plain English
└── PUSH_TO_GITHUB.sh                          ← Push updates to GitHub
```

---

## Adding Your Own Rules

1. Fill in `rules_template.md` or `rules_template.docx` in **plain English**
2. Upload it → receive a ready-to-use YAML rules file
3. Drop the file in `rules/` — picked up automatically on next run

---

## Jenkins Integration

```groovy
stage('IaC Quality Review') {
    steps {
        sh 'pip3 install -r requirements.txt'
        sh 'python3 reviewer.py ./ansible/ --min-severity error --output reports/review.html'
    }
    post {
        always {
            publishHTML(target: [reportName: 'IaC Review', reportDir: 'reports',
                                 reportFiles: 'review.html', keepAll: true])
        }
    }
}
```

Exits `1` on critical/error → fails the stage automatically.

---

## Pushing Updates to GitHub

```bash
cd InfrastructureAsCodeQualityCheck
git add -A
git commit -m "describe your change"
bash PUSH_TO_GITHUB.sh
```
