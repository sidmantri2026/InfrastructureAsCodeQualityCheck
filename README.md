# Ansible Code Reviewer

> Rule-driven static analysis for Ansible playbooks and roles.  
> Built for **Infrastructure Automation Teams** — consistent code reviews regardless of who runs them.

---

## What It Does

- Surfaces violations as **inline squiggly underlines + Problems panel** in VS Code
- Generates a rich **HTML report** for sharing in code reviews and Jenkins
- Produces a **0–100 quality score** and A–F grade per file
- Includes **56 rules** out of the box: naming, security, idempotency, style, and cloud security (AWS, Azure, GCP — sourced from KICS by Checkmarx)
- Exits with code `1` on critical/error violations — suitable as a Jenkins build gate
- **Zero AI, zero internet at review time** — runs fully offline on air-gapped VDIs

---

## Quick Start

```bash
pip3 install -r requirements.txt
python3 reviewer.py ./your-ansible-project/
```

---

## VS Code Extension (Recommended for Daily Use)

### Install (one-time per developer)

1. Open VS Code → `Ctrl+Shift+P` → **Extensions: Install from VSIX...**
2. Select `vscode-extension/ansible-code-reviewer-1.0.0.vsix`
3. Reload VS Code

### What you get after install

| Feature | Detail |
|---|---|
| **Auto review on save** | Save any `.yml` → violations appear instantly |
| **Inline squiggles** | Red = critical/error · Yellow = warning · Blue = info |
| **Problems panel** | Full list, filterable, with rule IDs |
| **Hover for rationale** | Hover any squiggle to see *why* the rule exists |
| **Status bar** | Shows live violation count; click to review |
| **Right-click menu** | Right-click YAML file → "🔍 Ansible Review: Current File" |
| **Keyboard shortcut** | `Ctrl+Shift+A` · Mac: `Cmd+Shift+A` |
| **HTML report** | Generated alongside diagnostics; open via Command Palette |

### Commands (`Ctrl+Shift+P` → type "Ansible")

```
🔍 Ansible Review: Current File
🔍 Ansible Review: Entire Workspace
📂 Ansible Review: Open Latest HTML Report
📋 Ansible Review: List All Rules
🧹 Ansible Review: Clear All Diagnostics
```

### Settings (`File → Preferences → Settings → search "Ansible Reviewer"`)

| Setting | Default | Description |
|---|---|---|
| `ansibleReviewer.pythonPath` | `python3` | Python interpreter path |
| `ansibleReviewer.reviewerScriptPath` | _(auto)_ | Path to reviewer.py (auto-detects from workspace root) |
| `ansibleReviewer.rulesDir` | _(auto)_ | Path to rules/ directory |
| `ansibleReviewer.minSeverity` | `info` | Minimum severity for Problems panel |
| `ansibleReviewer.runOnSave` | `true` | Auto-review on save |
| `ansibleReviewer.generateHtmlReport` | `true` | Also generate HTML on each run |
| `ansibleReviewer.showStatusBarItem` | `true` | Status bar visibility |

---

## Command Line Usage

```bash
python3 reviewer.py <path>                          # Review file or directory
python3 reviewer.py <path> --min-severity error     # Errors and criticals only
python3 reviewer.py <path> --output my-report.html  # Custom report path
python3 reviewer.py <path> --json                   # JSON output for CI
python3 reviewer.py --list-rules                    # List all loaded rules
```

---

## Project Structure

```
ansible-code-reviewer/
├── reviewer.py                               ← Core engine
├── requirements.txt                          ← pip3 install -r requirements.txt
├── rules/
│   ├── ansible_naming.yaml                   ←  5 rules: naming conventions
│   ├── ansible_security.yaml                 ←  5 rules: secrets, privilege escalation
│   ├── ansible_idempotency.yaml              ←  7 rules: FQCN, state, error handling
│   ├── ansible_style.yaml                    ←  7 rules: formatting, booleans
│   ├── ansible_security_kics_aws.yaml        ← 15 rules: AWS cloud security (KICS)
│   └── ansible_security_kics_azure_gcp.yaml  ← 13 rules: Azure, GCP, ansible.cfg (KICS)
├── vscode-extension/
│   ├── ansible-code-reviewer-1.0.0.vsix      ← Install this in VS Code
│   └── src/extension.ts                       ← Extension source (TypeScript)
├── sample_playbooks/
│   ├── bad_example.yml                        ← Triggers most rules
│   └── good_example.yml                       ← Compliant reference
├── rules_template.md                          ← Write new rules in plain English
├── rules_template.docx                        ← Word version of rules template
└── .vscode/tasks.json                         ← Fallback tasks (no extension needed)
```

---

## Rule Summary (56 total)

| Category | Count | Source |
|---|---|---|
| Naming Conventions | 5 | Team standards |
| Security (general) | 5 | Team standards |
| Idempotency & Structure | 7 | Team standards |
| Style & Formatting | 7 | Team standards |
| Cloud Security — AWS | 15 | KICS / Checkmarx |
| Cloud Security — Azure | 8 | KICS / Checkmarx |
| Cloud Security — GCP | 4 | KICS / Checkmarx |
| Ansible Config Security | 5 | KICS / Checkmarx |

---

## Adding Your Own Rules

1. Fill in `rules_template.md` or `rules_template.docx` in **plain English**
2. Upload the filled template → receive ready-to-use YAML rules file
3. Drop it in `rules/` — picked up on next run automatically

---

## Jenkins Integration

```groovy
stage('Ansible Code Review') {
    steps {
        sh 'pip3 install -r requirements.txt'
        sh 'python3 reviewer.py ./ansible/ --min-severity error --output reports/review.html'
    }
    post {
        always {
            publishHTML(target: [reportName: 'Ansible Review', reportDir: 'reports',
                                 reportFiles: 'review.html', keepAll: true])
        }
    }
}
```

Exits `1` on critical/error → fails the stage automatically.

---

## Severity Guide

| Level | Meaning | Jenkins |
|---|---|---|
| `critical` | Security risk / must fix before merge | ❌ Fails build |
| `error` | Clear standards violation | ❌ Fails build |
| `warning` | Best practice not followed | ✅ Passes |
| `info` | Style / documentation suggestion | ✅ Passes |

---

## Roadmap

- [x] Ansible playbooks/roles/tasks
- [x] VS Code extension with inline diagnostics
- [x] HTML report
- [x] Jenkins integration
- [x] KICS/Checkmarx security rules (AWS, Azure, GCP)
- [ ] Bash shell script rules
- [ ] PowerShell script rules
- [ ] Jenkins/Groovy pipeline rules
- [ ] AAP-specific rules
- [ ] Inline suppression comments (`# noqa ANS-SEC-001`)
