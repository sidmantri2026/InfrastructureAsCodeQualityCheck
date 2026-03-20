#!/usr/bin/env python3
"""
Ansible Code Reviewer
=====================
A rule-based static analysis tool for Ansible playbooks, roles, and task files.
Driven by YAML rulesets that your architects maintain.

Usage:
    python reviewer.py <path>           # Review a file or directory
    python reviewer.py <path> --html    # Generate HTML report (default)
    python reviewer.py <path> --json    # Output JSON results
    python reviewer.py --list-rules     # List all loaded rules
"""

import os
import re
import sys
import json
import yaml
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "error": 1, "warning": 2, "info": 3}
SEVERITY_EMOJI = {
    "critical": "🔴",
    "error":    "🟠",
    "warning":  "🟡",
    "info":     "🔵",
}

@dataclass
class Violation:
    rule_id:     str
    rule_name:   str
    severity:    str
    category:    str
    description: str
    rationale:   str
    file:        str
    line:        int
    line_content: str
    example_bad:  str = ""
    example_good: str = ""

@dataclass
class FileResult:
    filepath:   str
    violations: list = field(default_factory=list)

    @property
    def by_severity(self):
        counts = {"critical": 0, "error": 0, "warning": 0, "info": 0}
        for v in self.violations:
            counts[v.severity] = counts.get(v.severity, 0) + 1
        return counts

    @property
    def score(self):
        """0–100 score; higher is better."""
        weights = {"critical": 25, "error": 10, "warning": 3, "info": 1}
        penalty = sum(weights.get(v.severity, 0) for v in self.violations)
        return max(0, 100 - penalty)


# ─────────────────────────────────────────────────────────────────────────────
# Rule loader
# ─────────────────────────────────────────────────────────────────────────────

def load_rules(rules_dir: str) -> list[dict]:
    """Load all YAML rule files from the rules directory."""
    rules = []
    rules_path = Path(rules_dir)
    if not rules_path.exists():
        print(f"[ERROR] Rules directory not found: {rules_dir}")
        sys.exit(1)

    for yaml_file in sorted(rules_path.glob("*.yaml")):
        with open(yaml_file, "r") as f:
            data = yaml.safe_load(f)
        category = data.get("category", "General")
        technology = data.get("technology", "ansible")
        for rule in data.get("rules", []):
            rule["category"] = category
            rule["technology"] = technology
            rules.append(rule)

    return rules



# ─────────────────────────────────────────────────────────────────────────────
# .reviewer.yml config loader
# ─────────────────────────────────────────────────────────────────────────────

def load_reviewer_config(target: str) -> dict:
    """Load .reviewer.yml from the target directory (or its parents).
    Returns dict with keys: disabled_rules (set), severity_overrides (dict)."""
    config = {"disabled_rules": set(), "severity_overrides": {}}
    # Walk up from target path looking for .reviewer.yml
    search_path = Path(target) if Path(target).is_dir() else Path(target).parent
    for directory in [search_path] + list(search_path.parents):
        candidate = directory / ".reviewer.yml"
        if candidate.exists():
            try:
                data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                disabled = data.get("disabled_rules", []) or []
                config["disabled_rules"] = set(disabled)
                config["severity_overrides"] = data.get("severity_overrides", {}) or {}
                print(f"  [config] Loaded .reviewer.yml from {candidate}")
                print(f"  [config] {len(config['disabled_rules'])} rules disabled")
            except Exception as e:
                print(f"  [config] Warning: could not parse {candidate}: {e}")
            break  # stop at first found
    return config


# ─────────────────────────────────────────────────────────────────────────────
# Matchers
# ─────────────────────────────────────────────────────────────────────────────

def check_regex(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Apply a regex pattern across all lines."""
    violations = []
    pattern = re.compile(rule.get("pattern", ""), re.IGNORECASE if "IGNORECASE" in rule.get("flags", "") else 0)
    exclude_raw = rule.get("exclude_pattern")
    exclude = re.compile(exclude_raw, re.IGNORECASE) if exclude_raw else None

    for i, line in enumerate(lines, start=1):
        if pattern.search(line):
            if exclude and exclude.search(line):
                continue
            violations.append(Violation(
                rule_id=rule["id"],
                rule_name=rule["name"],
                severity=rule["severity"],
                category=rule["category"],
                description=rule["description"],
                rationale=rule.get("rationale", ""),
                file=filepath,
                line=i,
                line_content=line.rstrip(),
                example_bad=rule.get("example_bad", ""),
                example_good=rule.get("example_good", ""),
            ))
    return violations


def check_file_length(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Check that the file doesn't exceed a maximum number of lines."""
    violations = []
    max_lines = rule.get("max_lines", 150)
    if len(lines) > max_lines:
        violations.append(Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule["severity"],
            category=rule["category"],
            description=rule["description"],
            rationale=rule.get("rationale", ""),
            file=filepath,
            line=len(lines),
            line_content=f"File has {len(lines)} lines (limit: {max_lines})",
            example_bad=rule.get("example_bad", ""),
            example_good=rule.get("example_good", ""),
        ))
    return violations


def check_line_length(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Check individual line lengths."""
    violations = []
    max_len = rule.get("max_length", 160)
    for i, line in enumerate(lines, start=1):
        if len(line.rstrip()) > max_len:
            violations.append(Violation(
                rule_id=rule["id"],
                rule_name=rule["name"],
                severity=rule["severity"],
                category=rule["category"],
                description=rule["description"],
                rationale=rule.get("rationale", ""),
                file=filepath,
                line=i,
                line_content=line.rstrip()[:120] + f"  … ({len(line.rstrip())} chars)",
                example_bad=rule.get("example_bad", ""),
                example_good=rule.get("example_good", ""),
            ))
    return violations


def check_file_start(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Verify the file starts with a required prefix (e.g. ---)."""
    violations = []
    required = rule.get("required_start", "---")
    if not lines or not lines[0].startswith(required):
        violations.append(Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule["severity"],
            category=rule["category"],
            description=rule["description"],
            rationale=rule.get("rationale", ""),
            file=filepath,
            line=1,
            line_content=lines[0].rstrip() if lines else "(empty file)",
            example_bad=rule.get("example_bad", ""),
            example_good=rule.get("example_good", ""),
        ))
    return violations


def check_file_ending(rule: dict, content: str, filepath: str) -> list[Violation]:
    """Verify the file ends with a newline."""
    violations = []
    if rule.get("require_newline") and not content.endswith("\n"):
        violations.append(Violation(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule["severity"],
            category=rule["category"],
            description=rule["description"],
            rationale=rule.get("rationale", ""),
            file=filepath,
            line=-1,
            line_content="(end of file — missing newline)",
            example_bad=rule.get("example_bad", ""),
            example_good=rule.get("example_good", ""),
        ))
    return violations


def check_yaml_task_names(rule: dict, content: str, lines: list[str], filepath: str) -> list[Violation]:
    """Check that every task block has a 'name' field."""
    violations = []
    # Simple heuristic: look for module calls not preceded by a 'name:' in the same task block
    module_pattern = re.compile(r"^\s{2,}(ansible\.\w+\.\w+|\w+):\s*$")
    name_pattern   = re.compile(r"^\s*-\s*name:")

    task_start_pattern = re.compile(r"^\s*-\s+(?!name:|hosts:|become:|vars:|roles:|handlers:|block:|rescue:|always:|when:|tags:|register:|notify:|ignore_errors:|no_log:|with_|loop|include|import)")

    i = 0
    while i < len(lines):
        line = lines[i]
        if task_start_pattern.match(line) and not name_pattern.match(line):
            # Check next line to see if we're looking at a task without a name
            if i + 1 < len(lines) and module_pattern.match(lines[i + 1]):
                violations.append(Violation(
                    rule_id=rule["id"],
                    rule_name=rule["name"],
                    severity=rule["severity"],
                    category=rule["category"],
                    description=rule["description"],
                    rationale=rule.get("rationale", ""),
                    file=filepath,
                    line=i + 1,
                    line_content=lines[i].rstrip(),
                    example_bad=rule.get("example_bad", ""),
                    example_good=rule.get("example_good", ""),
                ))
        i += 1
    return violations


def check_play_name_missing(rule: dict, content: str, lines: list[str], filepath: str) -> list[Violation]:
    """Check that plays (top-level list items targeting 'hosts') have names."""
    violations = []
    hosts_pattern = re.compile(r"^\s{0,2}-\s+hosts:")
    name_pattern  = re.compile(r"^\s{0,2}-\s+name:")

    for i, line in enumerate(lines):
        if hosts_pattern.match(line):
            # Look back up to 5 lines for a name
            start = max(0, i - 5)
            block = lines[start:i]
            if not any(name_pattern.match(bl) for bl in block):
                violations.append(Violation(
                    rule_id=rule["id"],
                    rule_name=rule["name"],
                    severity=rule["severity"],
                    category=rule["category"],
                    description=rule["description"],
                    rationale=rule.get("rationale", ""),
                    file=filepath,
                    line=i + 1,
                    line_content=line.rstrip(),
                    example_bad=rule.get("example_bad", ""),
                    example_good=rule.get("example_good", ""),
                ))
    return violations


def check_play_level_become(rule: dict, content: str, lines: list[str], filepath: str) -> list[Violation]:
    """Flag become: true set at the play level (indented 2 or 4 spaces)."""
    violations = []
    become_pattern = re.compile(r"^(\s{0,4})become:\s*(true|yes)", re.IGNORECASE)
    hosts_seen = False

    for i, line in enumerate(lines):
        if re.match(r"^\s{0,2}-?\s*hosts:", line):
            hosts_seen = True
        if hosts_seen and become_pattern.match(line):
            indent = len(line) - len(line.lstrip())
            if indent <= 4:
                violations.append(Violation(
                    rule_id=rule["id"],
                    rule_name=rule["name"],
                    severity=rule["severity"],
                    category=rule["category"],
                    description=rule["description"],
                    rationale=rule.get("rationale", ""),
                    file=filepath,
                    line=i + 1,
                    line_content=line.rstrip(),
                    example_bad=rule.get("example_bad", ""),
                    example_good=rule.get("example_good", ""),
                ))
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Additional matchers for Bash / PowerShell
# ─────────────────────────────────────────────────────────────────────────────

def check_file_start_missing(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Check that file starts with one of the required patterns (e.g. shebang)."""
    violations = []
    patterns = rule.get("required_patterns", [])
    if not lines:
        violations.append(Violation(
            rule_id=rule["id"], rule_name=rule["name"], severity=rule["severity"],
            category=rule["category"], description=rule["description"],
            rationale=rule.get("rationale", ""), file=filepath, line=1,
            line_content="(empty file)",
            example_bad=rule.get("example_bad",""), example_good=rule.get("example_good",""),
        ))
        return violations
    first_line = lines[0].rstrip()
    matched = any(re.search(p, first_line) for p in patterns)
    if not matched:
        violations.append(Violation(
            rule_id=rule["id"], rule_name=rule["name"], severity=rule["severity"],
            category=rule["category"], description=rule["description"],
            rationale=rule.get("rationale", ""), file=filepath, line=1,
            line_content=first_line,
            example_bad=rule.get("example_bad",""), example_good=rule.get("example_good",""),
        ))
    return violations


def check_file_missing_pattern(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Check that a required pattern appears somewhere in the first N lines."""
    violations = []
    required = rule.get("required_pattern", "")
    search_lines = rule.get("search_lines", len(lines))
    if not required:
        return violations
    window = lines[:search_lines]
    found = any(re.search(required, ln, re.IGNORECASE) for ln in window)
    if not found:
        violations.append(Violation(
            rule_id=rule["id"], rule_name=rule["name"], severity=rule["severity"],
            category=rule["category"], description=rule["description"],
            rationale=rule.get("rationale", ""), file=filepath, line=1,
            line_content=f"(pattern '{required}' not found in first {search_lines} lines)",
            example_bad=rule.get("example_bad",""), example_good=rule.get("example_good",""),
        ))
    return violations


def check_require_nearby_in_file(rule: dict, lines: list[str], filepath: str) -> list[Violation]:
    """Regex match triggers a violation only when another pattern is absent anywhere in the file."""
    violations = []
    trigger_pat  = re.compile(rule.get("pattern",""), re.IGNORECASE if "IGNORECASE" in rule.get("flags","") else 0)
    require_pat_str = rule.get("require_nearby_in_file","")
    if not require_pat_str:
        return violations
    require_pat = re.compile(require_pat_str, re.IGNORECASE)
    file_has_required = any(require_pat.search(ln) for ln in lines)
    if file_has_required:
        return violations
    # File is missing the required pattern — flag every line matching trigger
    for i, line in enumerate(lines, 1):
        if trigger_pat.search(line):
            violations.append(Violation(
                rule_id=rule["id"], rule_name=rule["name"], severity=rule["severity"],
                category=rule["category"], description=rule["description"],
                rationale=rule.get("rationale",""), file=filepath, line=i,
                line_content=line.rstrip(),
                example_bad=rule.get("example_bad",""), example_good=rule.get("example_good",""),
            ))
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def apply_rule(rule: dict, content: str, lines: list[str], filepath: str) -> list[Violation]:
    match_type = rule.get("match_type", "")
    if match_type == "regex":
        return check_regex(rule, lines, filepath)
    elif match_type == "file_length":
        return check_file_length(rule, lines, filepath)
    elif match_type == "line_length":
        return check_line_length(rule, lines, filepath)
    elif match_type == "file_start":
        return check_file_start(rule, lines, filepath)
    elif match_type == "file_start_missing":
        return check_file_start_missing(rule, lines, filepath)
    elif match_type == "file_missing_pattern":
        return check_file_missing_pattern(rule, lines, filepath)
    elif match_type == "file_ending":
        return check_file_ending(rule, content, filepath)
    elif match_type == "yaml_key_missing":
        return check_yaml_task_names(rule, content, lines, filepath)
    elif match_type == "play_name_missing":
        return check_play_name_missing(rule, content, lines, filepath)
    elif match_type == "play_level_become":
        return check_play_level_become(rule, content, lines, filepath)
    elif match_type == "require_nearby_in_file":
        return check_require_nearby_in_file(rule, lines, filepath)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# File scanner — supports Ansible (.yml/.yaml), Bash (.sh), PowerShell (.ps1)
# ─────────────────────────────────────────────────────────────────────────────

TECH_EXTENSIONS = {
    "ansible":    {".yml", ".yaml"},
    "bash":       {".sh"},
    "powershell": {".ps1"},
    "jenkinsfile": {"jenkinsfile", ".groovy", ".jenkinsfile"},
}
ALL_EXTENSIONS  = {ext for exts in TECH_EXTENSIONS.values() for ext in exts}
IGNORE_DIRS     = {".git", ".tox", "__pycache__", "node_modules", ".venv", "venv"}

# Keep old name for backward compat
ANSIBLE_EXTENSIONS = TECH_EXTENSIONS["ansible"]
ANSIBLE_IGNORE_DIRS = IGNORE_DIRS

def get_file_technology(path: Path) -> str | None:
    """Return the technology name for a given file extension, or None if unsupported."""
    name = path.name.lower()
    ext  = path.suffix.lower()
    # Jenkinsfile: exact name match (Jenkinsfile, Jenkinsfile.prod, etc.)
    if name == "jenkinsfile" or name.startswith("jenkinsfile.") or ext in (".jenkinsfile", ".groovy"):
        return "jenkinsfile"
    for tech, exts in TECH_EXTENSIONS.items():
        if tech == "jenkinsfile":
            continue
        if ext in exts:
            return tech
    return None

def is_ansible_file(path: Path) -> bool:
    return path.suffix.lower() in TECH_EXTENSIONS["ansible"]

def gather_files(target: str) -> list[Path]:
    p = Path(target)
    if p.is_file():
        return [p] if get_file_technology(p) else []
    files = []
    for root, dirs, fnames in os.walk(p):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in fnames:
            fp = Path(root) / fname
            if get_file_technology(fp):
                files.append(fp)
    return sorted(files)

def review_file(filepath: Path, rules: list[dict]) -> FileResult:
    result = FileResult(filepath=str(filepath))
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return result
    lines = content.splitlines(keepends=True)
    tech  = get_file_technology(filepath)
    # Only apply rules whose technology matches this file
    matching_rules = [r for r in rules if r.get("technology","ansible") == tech]
    for rule in matching_rules:
        violations = apply_rule(rule, content, lines, str(filepath))
        result.violations.extend(violations)
    result.violations.sort(key=lambda v: v.line)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# HTML report generator  (v2 — interactive)
# ─────────────────────────────────────────────────────────────────────────────

def generate_html_report(results, rules, output_path, target):
    """Generate interactive HTML report. Data injected as JSON; all JS uses raw strings."""
    import json as _json

    total_violations = sum(len(r.violations) for r in results)
    total_files      = len(results)
    files_clean      = sum(1 for r in results if not r.violations)
    severity_totals  = {"critical": 0, "error": 0, "warning": 0, "info": 0}
    for r in results:
        for sev, cnt in r.by_severity.items():
            severity_totals[sev] += cnt
    avg_score = int(sum(r.score for r in results) / max(len(results), 1))
    if   avg_score >= 90: grade, grade_color = "A", "#22c55e"
    elif avg_score >= 75: grade, grade_color = "B", "#84cc16"
    elif avg_score >= 60: grade, grade_color = "C", "#eab308"
    elif avg_score >= 40: grade, grade_color = "D", "#f97316"
    else:                 grade, grade_color = "F", "#ef4444"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Build JSON payload (only Python f-strings here, no backticks) ──────
    files_payload = []
    for r in sorted(results, key=lambda x: x.score):
        viols = []
        for v in r.violations:
            viols.append({
                "rule_id":     v.rule_id,
                "rule_name":   v.rule_name,
                "severity":    v.severity,
                "category":    v.category,
                "description": v.description,
                "rationale":   v.rationale,
                "line":        v.line,
                "line_content":v.line_content,
                "example_bad": (v.example_bad  or "").strip(),
                "example_good":(v.example_good or "").strip(),
            })
        files_payload.append({
            "path":       r.filepath,
            "name":       Path(r.filepath).name,
            "score":      r.score,
            "violations": viols,
            "by_severity":r.by_severity,
        })

    rules_payload = [
        {"id": rl["id"], "name": rl["name"], "severity": rl["severity"],
         "category": rl.get("category",""), "description": rl.get("description",""),
         "rationale": rl.get("rationale","")}
        for rl in sorted(rules, key=lambda x: x["id"])
    ]

    report_data_json = _json.dumps({
        "target":    target,
        "timestamp": timestamp,
        "avg_score": avg_score,
        "grade":     grade,
        "grade_color": grade_color,
        "total_files": total_files,
        "files_clean": files_clean,
        "total_violations": total_violations,
        "severity_totals": severity_totals,
        "files": files_payload,
        "rules": rules_payload,
    }, ensure_ascii=False, indent=2)
    # Only escape </script> to prevent premature script tag termination inside JSON tag.
    # Backticks are safe inside <script type="application/json"> — browser never parses it as JS.
    report_data_json = report_data_json.replace("</script>", "</" + "script>")

    # ── CSS (f-string OK — no backticks) ────────────────────────────────────
    css = """
:root{--bg:#0a0c10;--surface:#111318;--surface2:#181c24;--surface3:#1e2330;
--border:#252b38;--border2:#2e3547;--text:#d4dae8;--muted:#5a6480;--muted2:#8090b0;
--accent:#3b8beb;--c-crit:#ff4560;--c-err:#ff8c42;--c-warn:#ffd166;--c-info:#06d6a0;
--c-clean:#22c55e;--mono:'Consolas','Menlo','Monaco','Courier New',monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI','Helvetica Neue',Arial,sans-serif;
--radius:6px;--radius-lg:12px;}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:var(--sans);background:var(--bg);color:var(--text);min-height:100vh;font-size:13px;line-height:1.6;}
::-webkit-scrollbar{width:6px;height:6px;}
::-webkit-scrollbar-track{background:var(--surface);}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px;}
.topbar{position:sticky;top:0;z-index:100;background:rgba(10,12,16,0.95);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 32px;display:flex;align-items:center;justify-content:space-between;height:56px;}
.topbar-left{display:flex;align-items:center;gap:14px;}
.logo-badge{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#1e6fd4,#3b8beb);display:flex;align-items:center;justify-content:center;font-size:15px;}
.logo-title{font-size:14px;font-weight:700;letter-spacing:.5px;}
.logo-sub{font-size:10px;color:var(--muted);font-family:var(--mono);letter-spacing:1px;text-transform:uppercase;}
.topbar-meta{font-size:11px;color:var(--muted);font-family:var(--mono);}
.topbar-meta span{color:var(--muted2);}
.topbar-right{display:flex;gap:20px;}
.layout{display:flex;min-height:calc(100vh - 56px);}
.sidebar{width:260px;flex-shrink:0;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;position:sticky;top:56px;height:calc(100vh - 56px);overflow-y:auto;}
.sidebar-section{padding:16px;border-bottom:1px solid var(--border);}
.sidebar-label{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:10px;font-family:var(--mono);}
.grade-wrap{display:flex;align-items:center;gap:16px;padding:4px 0 8px;}
.grade-ring{width:64px;height:64px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:800;border:3px solid var(--grade-col);color:var(--grade-col);box-shadow:0 0 20px color-mix(in srgb,var(--grade-col) 30%,transparent);}
.grade-info{flex:1;}
.grade-score{font-size:24px;font-weight:700;line-height:1;}
.grade-label{font-size:10px;color:var(--muted);margin-top:2px;font-family:var(--mono);}
.grade-bar-track{height:4px;background:var(--surface3);border-radius:2px;margin-top:8px;overflow:hidden;}
.grade-bar-fill{height:100%;border-radius:2px;background:linear-gradient(90deg,var(--c-crit),var(--c-warn),var(--c-clean));transition:width 1s cubic-bezier(.22,.68,0,1.2);}
.stat-row{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:2px;}
.stat-pill{background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;cursor:pointer;transition:all .15s;}
.stat-pill:hover,.stat-pill.active{border-color:var(--pill-color);box-shadow:0 0 0 1px var(--pill-color);}
.stat-pill.active{background:color-mix(in srgb,var(--pill-color) 10%,var(--surface2));}
.pill-val{font-size:20px;font-weight:700;color:var(--pill-color);line-height:1;font-family:var(--mono);}
.pill-name{font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-top:2px;}
.file-list{flex:1;overflow-y:auto;}
.file-list-item{display:flex;align-items:center;gap:8px;padding:9px 16px;cursor:pointer;border-left:2px solid transparent;transition:all .12s;font-family:var(--mono);font-size:11px;color:var(--muted2);}
.file-list-item:hover{background:var(--surface2);color:var(--text);}
.file-list-item.active{background:var(--surface2);border-left-color:var(--accent);color:var(--text);}
.file-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;background:var(--dot-col);}
.file-list-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.file-list-count{font-size:10px;padding:1px 5px;border-radius:3px;background:var(--surface3);color:var(--muted2);flex-shrink:0;}
.main{flex:1;overflow-y:auto;padding:28px 32px;}
.view-tabs{display:flex;gap:4px;margin-bottom:24px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:3px;width:fit-content;}
.view-tab{padding:6px 18px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;color:var(--muted);transition:all .15s;}
.view-tab.active{background:var(--surface3);color:var(--text);}
.filter-bar{display:flex;align-items:center;gap:8px;margin-bottom:20px;flex-wrap:wrap;}
.filter-chip{display:flex;align-items:center;gap:5px;padding:4px 12px;border-radius:100px;border:1px solid var(--border2);background:var(--surface);color:var(--muted);cursor:pointer;font-size:11px;font-weight:600;transition:all .15s;letter-spacing:.3px;font-family:var(--mono);}
.filter-chip:hover{border-color:var(--chip-col);color:var(--text);}
.filter-chip.active{background:color-mix(in srgb,var(--chip-col) 15%,var(--surface));border-color:var(--chip-col);color:var(--chip-col);}
.chip-dot{width:6px;height:6px;border-radius:50%;background:var(--chip-col);}
.filter-search{margin-left:auto;background:var(--surface);border:1px solid var(--border2);border-radius:var(--radius);padding:5px 12px;color:var(--text);font-family:var(--mono);font-size:11px;width:220px;outline:none;}
.filter-search:focus{border-color:var(--accent);}
.filter-search::placeholder{color:var(--muted);}
.issue-table{width:100%;border-collapse:collapse;}
.issue-table thead th{text-align:left;padding:8px 12px;font-size:9px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);font-family:var(--mono);border-bottom:1px solid var(--border2);cursor:pointer;user-select:none;white-space:nowrap;}
.issue-table thead th:hover{color:var(--text);}
.issue-table thead th.sorted .sort-icon{opacity:1;color:var(--accent);}
.issue-row{border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s;}
.issue-row:hover{background:var(--surface2);}
.issue-row.expanded{background:var(--surface2);}
.issue-row td{padding:10px 12px;vertical-align:top;}
.sev-tag{display:inline-flex;align-items:center;gap:4px;font-family:var(--mono);font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;letter-spacing:.8px;white-space:nowrap;border:1px solid var(--tag-border);color:var(--tag-color);background:var(--tag-bg);}
.sev-dot{width:5px;height:5px;border-radius:50%;background:var(--tag-color);}
.rule-id-tag{font-family:var(--mono);font-size:10px;color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent);border:1px solid color-mix(in srgb,var(--accent) 25%,transparent);padding:1px 6px;border-radius:3px;white-space:nowrap;}
.issue-name{font-size:12px;font-weight:600;color:var(--text);}
.issue-cat{font-size:10px;color:var(--muted);margin-top:2px;font-family:var(--mono);}
.file-ref{font-family:var(--mono);font-size:10px;color:var(--muted2);white-space:nowrap;}
.file-ref .line-badge{display:inline-block;background:var(--surface3);border:1px solid var(--border2);border-radius:3px;padding:0 5px;font-size:10px;margin-left:4px;color:var(--c-warn);}
.expand-icon{color:var(--muted);font-size:11px;transition:transform .2s;}
.issue-row.expanded .expand-icon{transform:rotate(180deg);}
.detail-row{display:none;}
.detail-row.open{display:table-row;}
.detail-row td{padding:0;}
.detail-inner{padding:20px 24px;background:var(--surface);border-bottom:2px solid var(--border2);display:grid;grid-template-columns:1fr 1fr;gap:20px;}
.detail-title{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:var(--muted);font-family:var(--mono);margin-bottom:8px;display:flex;align-items:center;gap:6px;}
.detail-title::before{content:'';display:inline-block;width:12px;height:1px;background:var(--border2);}
.detail-text{font-size:12px;color:var(--muted2);line-height:1.7;}
.rationale-box{background:color-mix(in srgb,var(--accent) 6%,var(--surface2));border:1px solid color-mix(in srgb,var(--accent) 18%,transparent);border-left:3px solid var(--accent);border-radius:var(--radius);padding:10px 14px;font-size:11px;color:var(--muted2);line-height:1.7;font-style:italic;}
.code-wrap{grid-column:1/-1;}
.code-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.code-block{border-radius:var(--radius);overflow:hidden;border:1px solid var(--border2);}
.code-block.bad{border-color:color-mix(in srgb,var(--c-crit) 30%,var(--border2));}
.code-block.good{border-color:color-mix(in srgb,var(--c-clean) 30%,var(--border2));}
.code-header{display:flex;align-items:center;gap:8px;padding:7px 14px;font-size:10px;font-weight:700;font-family:var(--mono);letter-spacing:.5px;border-bottom:1px solid var(--border2);}
.code-block.bad .code-header{background:color-mix(in srgb,var(--c-crit) 8%,var(--surface2));color:var(--c-crit);}
.code-block.good .code-header{background:color-mix(in srgb,var(--c-clean) 8%,var(--surface2));color:var(--c-clean);}
.offending-line{background:var(--surface2);border:1px solid var(--border2);border-radius:var(--radius);padding:10px 14px;display:flex;gap:12px;align-items:flex-start;font-family:var(--mono);font-size:11px;overflow-x:auto;margin-bottom:4px;}
.offending-line .ln{color:var(--muted);user-select:none;flex-shrink:0;padding-top:1px;border-right:1px solid var(--border2);padding-right:10px;min-width:32px;text-align:right;}
.offending-line code{color:var(--c-warn);flex:1;white-space:pre-wrap;word-break:break-all;}
.code-body{background:#080a0e;padding:12px 14px;}
.code-body pre{font-family:var(--mono);font-size:11px;white-space:pre-wrap;overflow-x:auto;line-height:1.7;}
.code-block.bad .code-body pre{color:#ff8585;}
.code-block.good .code-body pre{color:#85ffb0;}
.copy-btn{margin-left:auto;background:none;border:1px solid var(--border2);color:var(--muted);border-radius:3px;padding:2px 8px;font-size:9px;cursor:pointer;font-family:var(--mono);transition:all .15s;}
.copy-btn:hover{color:var(--text);border-color:var(--muted2);}
.file-card-v2{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);margin-bottom:12px;overflow:hidden;}
.file-card-header{display:flex;align-items:center;gap:16px;padding:14px 18px;cursor:pointer;user-select:none;transition:background .12s;}
.file-card-header:hover{background:var(--surface2);}
.file-score-ring{width:48px;height:48px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:800;font-family:var(--mono);border:2px solid var(--ring-col);color:var(--ring-col);box-shadow:0 0 12px color-mix(in srgb,var(--ring-col) 25%,transparent);}
.file-info{flex:1;min-width:0;}
.file-name-v2{font-size:13px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.file-path-v2{font-size:10px;color:var(--muted);font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px;}
.file-sev-pills{display:flex;gap:5px;margin-top:5px;flex-wrap:wrap;}
.file-sev-pill{font-size:9px;font-weight:700;padding:1px 6px;border-radius:3px;font-family:var(--mono);letter-spacing:.5px;border:1px solid var(--p-border);color:var(--p-color);background:var(--p-bg);}
.file-body-v2{display:none;border-top:1px solid var(--border);}
.file-card-v2.open .file-body-v2{display:block;}
.file-chevron{color:var(--muted);transition:transform .2s;flex-shrink:0;}
.file-card-v2.open .file-chevron{transform:rotate(180deg);}
.rules-table{width:100%;border-collapse:collapse;}
.rules-table th{text-align:left;padding:8px 14px;font-size:9px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted);font-family:var(--mono);border-bottom:1px solid var(--border2);}
.rules-table td{padding:10px 14px;border-bottom:1px solid var(--border);font-size:12px;vertical-align:top;}
.rules-table tr:last-child td{border-bottom:none;}
.rules-table tr:hover td{background:var(--surface2);}
.empty-state{text-align:center;padding:80px 40px;color:var(--muted);font-family:var(--mono);font-size:12px;}
.section-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}
.section-title{font-size:14px;font-weight:700;}
.section-count{font-family:var(--mono);font-size:11px;color:var(--muted);}
.footer{text-align:center;padding:32px;color:var(--muted);font-size:11px;border-top:1px solid var(--border);font-family:var(--mono);margin-top:40px;}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.issue-row,.file-card-v2{animation:fadeIn .2s ease both}
"""

    # ── JS — uses raw string so backslashes/backticks are never escaped ──────
    # NOTE: this is a raw Python string (r"""..."""). Do NOT change it to f-string.
    js_raw = r"""
const SEV_ORDER  = {critical:0,error:1,warning:2,info:3};
const SEV_COLORS = {critical:'var(--c-crit)',error:'var(--c-err)',warning:'var(--c-warn)',info:'var(--c-info)'};
const SEV_BG     = {
  critical:'color-mix(in srgb,var(--c-crit) 12%,var(--surface2))',
  error:   'color-mix(in srgb,var(--c-err) 10%,var(--surface2))',
  warning: 'color-mix(in srgb,var(--c-warn) 8%,var(--surface2))',
  info:    'color-mix(in srgb,var(--c-info) 8%,var(--surface2))',
};
const SEV_BORDER = {
  critical:'color-mix(in srgb,var(--c-crit) 35%,transparent)',
  error:   'color-mix(in srgb,var(--c-err) 30%,transparent)',
  warning: 'color-mix(in srgb,var(--c-warn) 25%,transparent)',
  info:    'color-mix(in srgb,var(--c-info) 25%,transparent)',
};
let currentFilter='all', currentSort={key:'severity',dir:1}, searchQuery='', activeFileIdx=null;

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

window.addEventListener('DOMContentLoaded',()=>{
  const D=REPORT_DATA;
  document.getElementById('hdr-target').textContent=D.target;
  document.getElementById('hdr-ts').textContent=D.timestamp;
  document.getElementById('hdr-rules').textContent=D.rules.length+' loaded';
  document.getElementById('grade-ring').textContent=D.grade;
  document.getElementById('grade-ring').style.setProperty('--grade-col',D.grade_color);
  document.getElementById('grade-score').textContent=D.avg_score+'/100';
  document.getElementById('grade-score').style.color=D.grade_color;
  document.getElementById('grade-bar').style.width=D.avg_score+'%';
  document.getElementById('stat-total').textContent=D.total_violations;
  document.getElementById('stat-clean').textContent=D.files_clean;
  document.getElementById('stat-crit').textContent=D.severity_totals.critical||0;
  document.getElementById('stat-err').textContent=D.severity_totals.error||0;
  document.getElementById('stat-warn').textContent=D.severity_totals.warning||0;
  document.getElementById('stat-info').textContent=D.severity_totals.info||0;
  buildSidebar();
  buildIssueTable();
  buildFilesView();
  buildRulesTable();
});

function buildSidebar(){
  const c=document.getElementById('sidebar-files');
  c.innerHTML=REPORT_DATA.files.map((f,i)=>{
    const col=f.score>=80?'var(--c-clean)':f.score>=60?'var(--c-warn)':'var(--c-crit)';
    return `<div class="file-list-item" style="--dot-col:${col}" id="sfl-${i}" onclick="focusFile(${i})">
      <div class="file-dot"></div>
      <div class="file-list-name" title="${esc(f.path)}">${esc(f.name)}</div>
      ${f.violations.length?`<div class="file-list-count">${f.violations.length}</div>`:''}
    </div>`;
  }).join('');
}

function focusFile(idx){
  document.querySelectorAll('.file-list-item').forEach(el=>el.classList.remove('active'));
  const el=document.getElementById('sfl-'+idx);
  if(el){el.classList.add('active');el.scrollIntoView({block:'nearest'});}
  activeFileIdx=idx;
  currentFilter='__file__'+idx;
  document.querySelectorAll('.filter-chip').forEach(c=>c.classList.remove('active'));
  switchView('issues');
  renderIssueTable();
}

function getAllViolations(){
  const all=[];
  REPORT_DATA.files.forEach((f,fi)=>{
    f.violations.forEach((v,vi)=>all.push({...v,_file:f.path,_fname:f.name,_fi:fi,_vi:vi}));
  });
  return all;
}

function getFilteredViolations(){
  let viols=getAllViolations();
  if(currentFilter.startsWith('__file__')){
    const fi=parseInt(currentFilter.replace('__file__',''));
    viols=viols.filter(v=>v._fi===fi);
  } else if(currentFilter!=='all'){
    viols=viols.filter(v=>v.severity===currentFilter);
  }
  if(searchQuery){
    const q=searchQuery.toLowerCase();
    viols=viols.filter(v=>
      v.rule_id.toLowerCase().includes(q)||v.rule_name.toLowerCase().includes(q)||
      v.description.toLowerCase().includes(q)||v.category.toLowerCase().includes(q)||
      v._fname.toLowerCase().includes(q));
  }
  viols.sort((a,b)=>{
    const k=currentSort.key;
    let av=k==='severity'?SEV_ORDER[a.severity]??9:(a[k]||a['_'+k]||'');
    let bv=k==='severity'?SEV_ORDER[b.severity]??9:(b[k]||b['_'+k]||'');
    if(av<bv)return -currentSort.dir;
    if(av>bv)return currentSort.dir;
    return 0;
  });
  return viols;
}

function buildIssueTable(){renderIssueTable();}

function renderIssueTable(){
  const viols=getFilteredViolations();
  const tbody=document.getElementById('issue-tbody');
  const noIssues=document.getElementById('no-issues');
  document.getElementById('issue-count-label').textContent=viols.length+' issue'+(viols.length!==1?'s':'');
  if(!viols.length){tbody.innerHTML='';noIssues.style.display='block';return;}
  noIssues.style.display='none';
  tbody.innerHTML=viols.map((v,idx)=>{
    const sc=SEV_COLORS[v.severity]||'var(--muted)';
    const sb=SEV_BG[v.severity]||'var(--surface2)';
    const sbo=SEV_BORDER[v.severity]||'var(--border)';
    const lineDisp=v.line>0?v.line:'EOF';
    return `
    <tr class="issue-row" id="irow-${idx}" onclick="toggleDetail(${idx})">
      <td><div class="sev-tag" style="--tag-color:${sc};--tag-bg:${sb};--tag-border:${sbo}">
        <span class="sev-dot"></span>${v.severity.toUpperCase()}</div></td>
      <td><span class="rule-id-tag">${esc(v.rule_id)}</span></td>
      <td><div class="issue-name">${esc(v.rule_name)}</div><div class="issue-cat">${esc(v.category)}</div></td>
      <td><div class="file-ref" title="${esc(v._file)}">${esc(v._fname)}<span class="line-badge">L${lineDisp}</span></div></td>
      <td><span class="expand-icon">&#9660;</span></td>
    </tr>
    <tr class="detail-row" id="detail-${idx}"><td colspan="5">${buildDetailHTML(v)}</td></tr>`;
  }).join('');
}

function buildDetailHTML(v){
  const lineDisp=v.line>0?v.line:'EOF';
  const lineContent=esc(v.line_content||'');
  const badCode=esc(v.example_bad||'');
  const goodCode=esc(v.example_good||'');
  const hasCode=badCode||goodCode;
  let html=`<div class="detail-inner">
    <div class="detail-section">
      <div class="detail-title">Description</div>
      <div class="detail-text">${esc(v.description)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-title">Why This Rule Exists</div>
      <div class="rationale-box">${esc(v.rationale)||'<span style="color:var(--muted)">No rationale provided.</span>'}</div>
    </div>`;
  if(lineContent){
    html+=`<div class="detail-section" style="grid-column:1/-1">
      <div class="detail-title">Offending Line &middot; ${esc(v._fname||v._file)} &middot; Line ${lineDisp}</div>
      <div class="offending-line"><span class="ln">${lineDisp}</span><code>${lineContent}</code></div>
    </div>`;
  }
  if(hasCode){
    html+=`<div class="code-wrap"><div class="detail-title" style="margin-bottom:10px">Code Fix Guide</div>
    <div class="code-grid">`;
    if(badCode){
      html+=`<div class="code-block bad">
        <div class="code-header">&#10007; &nbsp;Problematic Pattern
          <button class="copy-btn" onclick="copyCode(this,event)">copy</button></div>
        <div class="code-body"><pre>${badCode}</pre></div>
      </div>`;
    }
    if(goodCode){
      html+=`<div class="code-block good">
        <div class="code-header">&#10003; &nbsp;Recommended Fix
          <button class="copy-btn" onclick="copyCode(this,event)">copy</button></div>
        <div class="code-body"><pre>${goodCode}</pre></div>
      </div>`;
    }
    html+=`</div></div>`;
  }
  html+=`</div>`;
  return html;
}

function toggleDetail(idx){
  const row=document.getElementById('irow-'+idx);
  const detail=document.getElementById('detail-'+idx);
  const isOpen=detail.classList.contains('open');
  document.querySelectorAll('.detail-row.open').forEach(r=>r.classList.remove('open'));
  document.querySelectorAll('.issue-row.expanded').forEach(r=>r.classList.remove('expanded'));
  if(!isOpen){detail.classList.add('open');row.classList.add('expanded');}
}

function copyCode(btn,e){
  e.stopPropagation();
  const pre=btn.closest('.code-block').querySelector('pre');
  navigator.clipboard.writeText(pre.textContent).then(()=>{
    btn.textContent='copied!';setTimeout(()=>btn.textContent='copy',1500);
  });
}

function buildFilesView(){
  const container=document.getElementById('files-container');
  document.getElementById('file-count-label').textContent=REPORT_DATA.files.length+' files';
  container.innerHTML=REPORT_DATA.files.map((f,fi)=>{
    const score=f.score;
    const ringCol=score>=80?'var(--c-clean)':score>=60?'var(--c-warn)':'var(--c-crit)';
    const sev=f.by_severity||{};
    const pills=['critical','error','warning','info'].filter(s=>sev[s]>0).map(s=>{
      const col=SEV_COLORS[s];const bg=SEV_BG[s];const bo=SEV_BORDER[s];
      return `<div class="file-sev-pill" style="--p-color:${col};--p-bg:${bg};--p-border:${bo}">${s[0].toUpperCase()}:${sev[s]}</div>`;
    }).join('');
    const rows=f.violations.map((v,vi)=>{
      const sc=SEV_COLORS[v.severity]||'var(--muted)';
      const sb=SEV_BG[v.severity];const sbo=SEV_BORDER[v.severity];
      const lineDisp=v.line>0?v.line:'EOF';
      const did=`fv-${fi}-${vi}`;
      return `
      <tr class="issue-row" id="frow-${did}" onclick="toggleFDetail('${did}')">
        <td><div class="sev-tag" style="--tag-color:${sc};--tag-bg:${sb};--tag-border:${sbo}">
          <span class="sev-dot"></span>${v.severity.toUpperCase()}</div></td>
        <td><span class="rule-id-tag">${esc(v.rule_id)}</span></td>
        <td><div class="issue-name">${esc(v.rule_name)}</div><div class="issue-cat">${esc(v.category)}</div></td>
        <td><div class="file-ref"><span class="line-badge">L${lineDisp}</span></div></td>
        <td><span class="expand-icon">&#9660;</span></td>
      </tr>
      <tr class="detail-row" id="fdetail-${did}">
        <td colspan="5">${buildDetailHTML({...v,_fname:f.name,_file:f.path})}</td>
      </tr>`;
    }).join('');
    return `
    <div class="file-card-v2" id="fcard-${fi}">
      <div class="file-card-header" onclick="toggleFileCard(${fi})">
        <div class="file-score-ring" style="--ring-col:${ringCol}">${score}</div>
        <div class="file-info">
          <div class="file-name-v2">${esc(f.name)}</div>
          <div class="file-path-v2">${esc(f.path)}</div>
          <div class="file-sev-pills">${pills||'<span style="color:var(--c-clean);font-size:10px">&#10003; Clean</span>'}</div>
        </div>
        <div style="flex-shrink:0;color:var(--muted);font-size:11px;font-family:var(--mono);margin-right:8px">
          ${f.violations.length} issue${f.violations.length!==1?'s':''}</div>
        <div class="file-chevron">&#9660;</div>
      </div>
      <div class="file-body-v2">
        ${f.violations.length?
          `<table class="issue-table" style="margin:0"><thead><tr>
            <th>Severity</th><th>Rule ID</th><th>Issue</th><th>Line</th><th></th>
          </tr></thead><tbody>${rows}</tbody></table>`
          :`<div class="empty-state" style="padding:30px"><div style="font-size:28px;margin-bottom:8px">&#10003;</div>No violations</div>`
        }
      </div>
    </div>`;
  }).join('');
}

function toggleFileCard(fi){document.getElementById('fcard-'+fi).classList.toggle('open');}
function toggleFDetail(id){
  const row=document.getElementById('frow-'+id);
  const detail=document.getElementById('fdetail-'+id);
  const isOpen=detail.classList.contains('open');
  document.querySelectorAll('.detail-row.open').forEach(r=>r.classList.remove('open'));
  document.querySelectorAll('.issue-row.expanded').forEach(r=>r.classList.remove('expanded'));
  if(!isOpen){detail.classList.add('open');row.classList.add('expanded');}
}

function buildRulesTable(){
  document.getElementById('rules-count-label').textContent=REPORT_DATA.rules.length+' rules';
  document.getElementById('rules-tbody').innerHTML=REPORT_DATA.rules.map(r=>{
    const sc=SEV_COLORS[r.severity]||'var(--muted)';
    const sb=SEV_BG[r.severity];const sbo=SEV_BORDER[r.severity];
    return `<tr>
      <td><span class="rule-id-tag">${esc(r.id)}</span></td>
      <td><div class="sev-tag" style="--tag-color:${sc};--tag-bg:${sb};--tag-border:${sbo}">
        <span class="sev-dot"></span>${r.severity.toUpperCase()}</div></td>
      <td style="color:var(--muted);font-family:var(--mono);font-size:10px">${esc(r.category)}</td>
      <td>${esc(r.name)}</td>
    </tr>`;
  }).join('');
}

function setFilter(sev){
  currentFilter=sev;
  document.querySelectorAll('.filter-chip').forEach(c=>{
    c.classList.toggle('active',c.dataset.sev===sev);
  });
  switchView('issues');
  renderIssueTable();
}

function applySearch(){searchQuery=document.getElementById('search-box').value;renderIssueTable();}

function sortTable(key){
  if(currentSort.key===key)currentSort.dir*=-1;
  else{currentSort.key=key;currentSort.dir=1;}
  document.querySelectorAll('.issue-table thead th').forEach(th=>th.classList.remove('sorted'));
  const th=document.getElementById('th-'+key);
  if(th)th.classList.add('sorted');
  renderIssueTable();
}

function switchView(name){
  ['issues','files','rules'].forEach(v=>{
    document.getElementById('view-'+v).style.display=v===name?'':'none';
    document.getElementById('tab-'+v).classList.toggle('active',v===name);
  });
}
"""

    # ── Assemble HTML (f-string safe — no backticks in this section) ─────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>IaC Quality Review Report</title>

<style>{css}</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-left">
    <div class="logo-badge">&#9881;</div>
    <div>
      <div class="logo-title">IaC Quality Reviewer</div>
      <div class="logo-sub">Infrastructure Automation &middot; Quality Gate</div>
    </div>
  </div>
  <div class="topbar-right">
    <div class="topbar-meta">target <span id="hdr-target"></span></div>
    <div class="topbar-meta">generated <span id="hdr-ts"></span></div>
    <div class="topbar-meta">rules <span id="hdr-rules"></span></div>
  </div>
</div>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-label">Overall Grade</div>
      <div class="grade-wrap">
        <div class="grade-ring" id="grade-ring"></div>
        <div class="grade-info">
          <div class="grade-score" id="grade-score"></div>
          <div class="grade-label">avg quality score</div>
          <div class="grade-bar-track"><div class="grade-bar-fill" id="grade-bar"></div></div>
        </div>
      </div>
    </div>
    <div class="sidebar-section">
      <div class="sidebar-label">Summary</div>
      <div class="stat-row">
        <div class="stat-pill" style="--pill-color:var(--text)" onclick="setFilter('all')"><div class="pill-val" id="stat-total">0</div><div class="pill-name">All Issues</div></div>
        <div class="stat-pill" style="--pill-color:var(--c-clean)"><div class="pill-val" id="stat-clean" style="color:var(--c-clean)">0</div><div class="pill-name">Clean Files</div></div>
        <div class="stat-pill" style="--pill-color:var(--c-crit)" onclick="setFilter('critical')"><div class="pill-val" id="stat-crit" style="color:var(--c-crit)">0</div><div class="pill-name">Critical</div></div>
        <div class="stat-pill" style="--pill-color:var(--c-err)" onclick="setFilter('error')"><div class="pill-val" id="stat-err" style="color:var(--c-err)">0</div><div class="pill-name">Error</div></div>
        <div class="stat-pill" style="--pill-color:var(--c-warn)" onclick="setFilter('warning')"><div class="pill-val" id="stat-warn" style="color:var(--c-warn)">0</div><div class="pill-name">Warning</div></div>
        <div class="stat-pill" style="--pill-color:var(--c-info)" onclick="setFilter('info')"><div class="pill-val" id="stat-info" style="color:var(--c-info)">0</div><div class="pill-name">Info</div></div>
      </div>
    </div>
    <div class="sidebar-section" style="padding-bottom:8px;"><div class="sidebar-label">Files</div></div>
    <div class="file-list" id="sidebar-files"></div>
  </aside>
  <main class="main">
    <div class="view-tabs">
      <div class="view-tab active" onclick="switchView('issues')" id="tab-issues">Issues</div>
      <div class="view-tab" onclick="switchView('files')" id="tab-files">By File</div>
      <div class="view-tab" onclick="switchView('rules')" id="tab-rules">Rule Reference</div>
    </div>
    <div id="view-issues">
      <div class="filter-bar">
        <div class="filter-chip active" style="--chip-col:var(--text)" onclick="setFilter('all')" data-sev="all">All</div>
        <div class="filter-chip" style="--chip-col:var(--c-crit)" onclick="setFilter('critical')" data-sev="critical"><span class="chip-dot"></span>Critical</div>
        <div class="filter-chip" style="--chip-col:var(--c-err)" onclick="setFilter('error')" data-sev="error"><span class="chip-dot"></span>Error</div>
        <div class="filter-chip" style="--chip-col:var(--c-warn)" onclick="setFilter('warning')" data-sev="warning"><span class="chip-dot"></span>Warning</div>
        <div class="filter-chip" style="--chip-col:var(--c-info)" onclick="setFilter('info')" data-sev="info"><span class="chip-dot"></span>Info</div>
        <input class="filter-search" id="search-box" placeholder="Search rule, file, description&hellip;" oninput="applySearch()">
      </div>
      <div class="section-hdr">
        <div class="section-title">Issues</div>
        <div class="section-count" id="issue-count-label"></div>
      </div>
      <table class="issue-table" id="issue-table">
        <thead><tr>
          <th onclick="sortTable('severity')" id="th-severity">Severity <span class="sort-icon">&#8597;</span></th>
          <th onclick="sortTable('rule_id')" id="th-rule_id">Rule ID <span class="sort-icon">&#8597;</span></th>
          <th onclick="sortTable('rule_name')" id="th-rule_name">Issue <span class="sort-icon">&#8597;</span></th>
          <th onclick="sortTable('file')" id="th-file">File &amp; Line <span class="sort-icon">&#8597;</span></th>
          <th></th>
        </tr></thead>
        <tbody id="issue-tbody"></tbody>
      </table>
      <div class="empty-state" id="no-issues" style="display:none">
        <div style="font-size:48px;margin-bottom:16px;opacity:.5">&#10003;</div>No issues match the current filter.
      </div>
    </div>
    <div id="view-files" style="display:none">
      <div class="section-hdr"><div class="section-title">Files</div><div class="section-count" id="file-count-label"></div></div>
      <div id="files-container"></div>
    </div>
    <div id="view-rules" style="display:none">
      <div class="section-hdr"><div class="section-title">Rule Reference</div><div class="section-count" id="rules-count-label"></div></div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);overflow:hidden;">
        <table class="rules-table"><thead><tr><th>ID</th><th>Severity</th><th>Category</th><th>Rule Name</th></tr></thead>
        <tbody id="rules-tbody"></tbody></table>
      </div>
    </div>
    <div class="footer">Generated by <strong>IaC Quality Reviewer</strong> &mdash; Infrastructure Automation Team</div>
  </main>
</div>
<script type="application/json" id="report-data">{report_data_json}</script>
<script>
const REPORT_DATA = JSON.parse(document.getElementById('report-data').textContent);
{js_raw}
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)



# ─────────────────────────────────────────────────────────────────────────────
# Interactive new-rule wizard
# ─────────────────────────────────────────────────────────────────────────────

def run_new_rule_wizard(rules_dir: str):
    """Interactive CLI wizard to create a new rule. Appends to rules/<tech>_custom.yaml."""
    import readline  # enables arrow keys / edit in input()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          IaC Quality Reviewer — New Rule Wizard         ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    def ask(prompt, options=None, default=None):
        if options:
            print(f"  {prompt}")
            for i, opt in enumerate(options, 1):
                marker = " (default)" if opt == default else ""
                print(f"    {i}. {opt}{marker}")
            while True:
                raw = input("  Enter number or value: ").strip()
                if not raw and default:
                    return default
                if raw.isdigit() and 1 <= int(raw) <= len(options):
                    return options[int(raw) - 1]
                if raw in options:
                    return raw
                print("  ⚠ Invalid choice, try again.")
        else:
            val = input(f"  {prompt}{' [' + default + ']' if default else ''}: ").strip()
            return val if val else (default or "")

    def ask_multiline(prompt):
        print(f"  {prompt}")
        print("  (Paste your code. Enter an empty line to finish.)")
        lines = []
        while True:
            line = input("  > ")
            if line == "" and lines:
                break
            lines.append(line)
        return "\n".join(lines)

    # ── Step 1: Technology ───────────────────────────────────────────────────
    print("── Step 1/7: Technology ─────────────────────────────────────")
    tech = ask("Which technology does this rule apply to?",
               ["ansible", "bash", "powershell", "jenkinsfile"])

    # ── Step 2: Category ─────────────────────────────────────────────────────
    print()
    print("── Step 2/7: Category ───────────────────────────────────────")
    tech_label = {"ansible":"Ansible","bash":"Bash","powershell":"PowerShell","jenkinsfile":"Jenkinsfile"}[tech]
    categories = {
        "ansible":    ["Linting & Style","Security","Idempotency","Hardcoding & Sensitive Data","Custom"],
        "bash":       ["Linting & Style","Security","Error Handling","Idempotency","Hardcoding & Sensitive Data","Custom"],
        "powershell": ["Linting & Style","Security","Error Handling","Idempotency","Hardcoding & Sensitive Data","Custom"],
        "jenkinsfile":["Linting & Style","Security","Error Handling","Idempotency","Hardcoding & Sensitive Data","Custom"],
    }
    short_cat = ask("Which category does this rule belong to?", categories[tech])
    category  = f"{tech_label} - {short_cat}"

    # ── Step 3: Rule name & ID ───────────────────────────────────────────────
    print()
    print("── Step 3/7: Rule Name ──────────────────────────────────────")
    rule_name = ask("Short descriptive name for the rule (e.g. 'Tasks must have a name'):")
    while not rule_name:
        print("  ⚠ Rule name cannot be empty.")
        rule_name = ask("Short descriptive name:")

    # ── Pick target rules file ───────────────────────────────────────────────
    rules_dir_path = Path(rules_dir)
    # Find all existing rule files for this technology
    tech_files = sorted([
        f for f in rules_dir_path.glob("*.yaml")
        if f.stem.startswith(tech)
    ])
    NEW_FILE_OPTION = f"[Create new file: {tech}_custom.yaml]"
    file_choices = [f.name for f in tech_files] + [NEW_FILE_OPTION]

    print()
    print("── Step 3b/7: Choose Rules File ─────────────────────────────")
    print("  Which file should this rule be added to?")
    chosen_name = ask("Select a file to append this rule to:", file_choices,
                      default=NEW_FILE_OPTION)
    if chosen_name == NEW_FILE_OPTION:
        target_file = rules_dir_path / f"{tech}_custom.yaml"
    else:
        target_file = rules_dir_path / chosen_name
    print(f"  → Will save to: {target_file.name}")

    # Auto-generate ID using ALL existing IDs in target file as reference
    prefix_map = {"ansible":"ANS","bash":"BASH","powershell":"PS","jenkinsfile":"JNK"}
    cat_map     = {"Linting & Style":"LINT","Security":"SEC","Error Handling":"ERR",
                   "Idempotency":"IDEM","Hardcoding & Sensitive Data":"HARD","Custom":"CUST"}
    prefix = f"{prefix_map[tech]}-{cat_map.get(short_cat,'CUST')}"
    # Scan ALL rule files for this tech to avoid duplicate numbers
    all_existing_ids = []
    for f in tech_files:
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            all_existing_ids += [r.get("id","") for r in d.get("rules", [])]
        except Exception:
            pass
    nums = [int(id_.split("-")[-1]) for id_ in all_existing_ids
            if id_.startswith(prefix + "-") and id_.split("-")[-1].isdigit()]
    next_num = (max(nums) + 1) if nums else 1
    rule_id  = f"{prefix}-{next_num:03d}"
    print()
    print(f"  → Auto-generated Rule ID: {rule_id}")
    custom_id = ask("Accept this ID or enter a custom one:", [rule_id, "Enter custom ID"])
    if custom_id == "Enter custom ID":
        custom_id = ask("Enter custom Rule ID:") or rule_id
        rule_id = custom_id
    else:
        rule_id = custom_id

    # ── Step 4: Severity ─────────────────────────────────────────────────────
    print()
    print("── Step 4/7: Severity ───────────────────────────────────────")
    print("  CRITICAL — Security risk / must fix before merge  (blocks Jenkins)")
    print("  ERROR    — Clear standards violation              (blocks Jenkins)")
    print("  WARNING  — Best practice not followed             (Jenkins passes)")
    print("  INFO     — Style / documentation suggestion       (Jenkins passes)")
    severity = ask("Choose severity:", ["critical","error","warning","info"], default="warning")

    # ── Step 5: Description & rationale ──────────────────────────────────────
    print()
    print("── Step 5/7: Description ────────────────────────────────────")
    description = ask("What does this rule check? (plain English, one sentence):")
    while not description:
        description = ask("Description cannot be empty:")

    print()
    rationale = ask("Why does this rule exist? (shown to developers in reports):")

    # ── Step 6: Match pattern ────────────────────────────────────────────────
    print()
    print("── Step 6/7: Detection Pattern ──────────────────────────────")
    match_type = ask("How should this rule detect violations?", [
        "regex           — search for a pattern in file lines",
        "file_missing_pattern — flag file if a required pattern is absent",
        "file_length      — flag file if it exceeds a max line count",
        "line_length      — flag lines exceeding a max character count",
    ])
    match_type = match_type.split()[0]

    pattern = ""
    extra   = {}
    if match_type == "regex":
        print()
        print("  Enter a Python regex pattern that matches the PROBLEMATIC code.")
        print("  Examples:  eval\\s+  |  hardcoded_password\\s*=  |  ^\\s*rm\\s+-rf")
        pattern = ask("Regex pattern (leave blank to fill in manually later):")
        flags   = ask("Case insensitive?", ["no", "yes"], default="no")
        if flags == "yes":
            extra["flags"] = "IGNORECASE"
        excl = ask("Exclude pattern (lines matching this will be skipped, leave blank if none):")
        if excl:
            extra["exclude_pattern"] = excl
    elif match_type == "file_missing_pattern":
        pattern = ask("Required pattern (file is flagged if this is NOT found):")
        extra["required_pattern"] = pattern
        extra["search_lines"] = 20
        pattern = ""
    elif match_type == "file_length":
        max_l = ask("Maximum allowed lines:", default="200")
        extra["max_lines"] = int(max_l) if max_l.isdigit() else 200
    elif match_type == "line_length":
        max_l = ask("Maximum line length (characters):", default="160")
        extra["max_length"] = int(max_l) if max_l.isdigit() else 160

    # ── Step 7: Examples ─────────────────────────────────────────────────────
    print()
    print("── Step 7/7: Code Examples ───────────────────────────────────")
    print("  These appear in the HTML report and VS Code hover text.")
    want_examples = ask("Add code examples?", ["yes","no"], default="yes")

    example_bad  = ""
    example_good = ""
    if want_examples == "yes":
        print()
        example_bad  = ask_multiline("Paste an example of BAD/problematic code:")
        print()
        example_good = ask_multiline("Paste an example of GOOD/recommended code:")

    # ── Build YAML rule ───────────────────────────────────────────────────────
    rule = {
        "id":          rule_id,
        "name":        rule_name,
        "severity":    severity,
        "description": description,
    }
    if rationale:
        rule["rationale"] = rationale
    rule["match_type"] = match_type
    if pattern:
        rule["pattern"] = pattern
    rule.update(extra)
    if example_bad:
        rule["example_bad"] = example_bad + "\n"
    if example_good:
        rule["example_good"] = example_good + "\n"

    # ── Show preview ──────────────────────────────────────────────────────────
    print()
    print("── Preview ───────────────────────────────────────────────────")
    preview = yaml.dump([rule], default_flow_style=False, allow_unicode=True, sort_keys=False)
    for line in preview.split("\n")[:25]:
        print(f"  {line}")
    if len(preview.split("\n")) > 25:
        print("  ...")
    print()

    confirm = ask("Save this rule?", ["yes","no","edit manually"], default="yes")
    if confirm == "no":
        print("  Cancelled — no rule was saved.")
        return
    if confirm == "edit manually":
        print()
        print(f"  → Add the YAML above to: {target_file}")
        print(f"  → Then run: python3 reviewer.py --list-rules | grep {rule_id}")
        return

    # ── Write to chosen file ──────────────────────────────────────────────────
    if target_file.exists():
        existing = yaml.safe_load(target_file.read_text(encoding="utf-8")) or {}
        if "rules" not in existing:
            existing["rules"] = []
    else:
        existing = {
            "category":    category,
            "technology":  tech,
            "description": f"Custom rules for {tech_label}.",
            "rules":       []
        }

    existing["rules"].append(rule)
    target_file.write_text(
        yaml.dump(existing, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8"
    )

    total_in_file = len(existing["rules"])
    print()
    print(f"  ✅  Rule {rule_id} saved to: {target_file.name}")
    print(f"      That file now contains {total_in_file} rule(s).")
    print()
    print("  ── Test it now ──────────────────────────────────────────────")
    print(f"  python3 reviewer.py --list-rules | grep {rule_id}")
    print(f"  python3 reviewer.py sample_playbooks/ --min-severity {severity}")
    print()

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Ansible Code Reviewer — consistent, rule-driven static analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("target", nargs="?", default=".", help="File or directory to review")
    parser.add_argument("--rules-dir", default=str(Path(__file__).parent / "rules"), help="Path to rules directory")
    parser.add_argument("--output", "-o", default="", help="Output path for HTML report (default: reports/review_<timestamp>.html)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of HTML")
    parser.add_argument("--list-rules", action="store_true", help="List all loaded rules and exit")
    parser.add_argument("--min-severity", choices=["critical","error","warning","info"], default="info", help="Minimum severity to report")
    parser.add_argument("--new-rule", action="store_true", help="Launch interactive wizard to create a new rule")
    parser.add_argument("--rule-manager", action="store_true", help="Open the HTML rule manager GUI in your browser")
    args = parser.parse_args()

    rules = load_rules(args.rules_dir)

    if args.new_rule:
        run_new_rule_wizard(args.rules_dir)
        return

    if args.rule_manager:
        import webbrowser
        manager_path = Path(__file__).parent / "rule_manager.html"
        if manager_path.exists():
            webbrowser.open(f"file://{manager_path}")
            print(f"✅ Opening Rule Manager: {manager_path}")
        else:
            print(f"❌ rule_manager.html not found at: {manager_path}")
            print("   Download it from the repository.")
        return

    if args.list_rules:
        print(f"\n{'ID':<18} {'SEV':<10} {'CATEGORY':<25} {'NAME'}")
        print("─" * 90)
        for r in sorted(rules, key=lambda x: x["id"]):
            print(f"{r['id']:<18} {r['severity']:<10} {r.get('category',''):<25} {r['name']}")
        print(f"\n{len(rules)} rules loaded from {args.rules_dir}")
        return

    target = args.target
    files  = gather_files(target)

    if not files:
        print(f"No supported files found at: {target}")

    # Load .reviewer.yml config (disabled rules, severity overrides)
    reviewer_cfg = load_reviewer_config(target)
    disabled_ids = reviewer_cfg["disabled_rules"]
    if disabled_ids:
        rules = [r for r in rules if r["id"] not in disabled_ids]
        print(f"  [config] {len(disabled_ids)} rules disabled by .reviewer.yml")

    results = []
    sev_idx = SEVERITY_ORDER[args.min_severity]
    for fp in files:
        result = review_file(fp, rules)
        # Filter by min severity
        result.violations = [v for v in result.violations if SEVERITY_ORDER.get(v.severity, 99) <= sev_idx]
        results.append(result)

    if args.json:
        output = [{"file": r.filepath, "score": r.score, "violations": [asdict(v) for v in r.violations]} for r in results]
        print(json.dumps(output, indent=2))
        return

    # HTML report
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or str(report_dir / f"review_{ts}.html")

    generate_html_report(results, rules, output_path, target)

    # Console summary
    total = sum(len(r.violations) for r in results)
    print(f"\n{'═'*55}")
    print(f"  Ansible Code Review Complete")
    print(f"{'═'*55}")
    print(f"  Files scanned : {len(results)}")
    print(f"  Total issues  : {total}")
    for sev in ["critical","error","warning","info"]:
        cnt = sum(r.by_severity.get(sev,0) for r in results)
        if cnt:
            print(f"  {SEVERITY_EMOJI[sev]} {sev.capitalize():<10}: {cnt}")
    print(f"  Report        : {output_path}")
    print(f"{'═'*55}\n")

    # Exit code: non-zero if critical/error found
    critical_errors = sum(r.by_severity.get("critical",0) + r.by_severity.get("error",0) for r in results)
    sys.exit(1 if critical_errors else 0)


if __name__ == "__main__":
    main()
