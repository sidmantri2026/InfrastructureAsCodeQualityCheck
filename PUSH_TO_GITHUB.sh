#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# One-time script to push this project to YOUR GitHub account
# Usage: bash PUSH_TO_GITHUB.sh
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_USERNAME="sidmantri2026"
REPO_NAME="InfrastructureAsCodeQualityCheck"
REPO_DESC="Rule-driven static analysis for Ansible — 56 rules, VS Code extension, HTML report, Jenkins integration"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  Ansible Code Reviewer — GitHub Push Setup"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "This script will:"
echo "  1. Create the GitHub repo: $GITHUB_USERNAME/$REPO_NAME"
echo "  2. Push all code with a single initial commit"
echo ""
echo "You need a GitHub Personal Access Token with 'repo' scope."
echo "Get one at: https://github.com/settings/tokens/new"
echo "  → Select: 'repo' (full control of private repositories)"
echo ""
read -sp "Paste your GitHub token and press Enter: " TOKEN
echo ""

if [ -z "$TOKEN" ]; then
  echo "ERROR: No token provided."
  exit 1
fi

# Create the repo
echo "→ Creating repository $REPO_NAME..."
CREATE_RESPONSE=$(curl -s -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/user/repos \
  -d "{\"name\":\"$REPO_NAME\",\"description\":\"$REPO_DESC\",\"private\":false,\"auto_init\":false}")

CLONE_URL=$(echo "$CREATE_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('clone_url',''))" 2>/dev/null)

if [ -z "$CLONE_URL" ]; then
  echo "  Repo may already exist — attempting to push to existing repo..."
  CLONE_URL="https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"
fi

echo "  Remote: $CLONE_URL"

# Configure remote with token embedded
AUTH_URL="https://${TOKEN}@github.com/${GITHUB_USERNAME}/${REPO_NAME}.git"
git remote add origin "$AUTH_URL" 2>/dev/null || git remote set-url origin "$AUTH_URL"

# Push
echo "→ Pushing to GitHub..."
git push -u origin main

if [ $? -eq 0 ]; then
  echo ""
  echo "✅ Success! Your repo is live at:"
  echo "   https://github.com/$GITHUB_USERNAME/$REPO_NAME"
  echo ""
  echo "Next steps:"
  echo "  1. Add topics in GitHub: ansible, iac, security, automation, linting"
  echo "  2. Install the VS Code extension from vscode-extension/"
  echo "  3. Share the repo URL with your team"
else
  echo ""
  echo "❌ Push failed. Check your token has 'repo' scope and try again."
fi

# Clean token from remote URL after push
git remote set-url origin "https://github.com/$GITHUB_USERNAME/$REPO_NAME.git"
