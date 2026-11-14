#!/usr/bin/env bash
# 授权后一键：在 GitHub 创建仓库并推送 main 分支。
# 前置：已通过 `gh auth login` 登录，或已导出 GH_TOKEN（带 repo 权限的 PAT）。
set -euo pipefail

REPO_NAME="${REPO_NAME:-soqq-search-api}"
VISIBILITY="${VISIBILITY:-private}"   # private 或 public
REMOTE_URL="${REMOTE_URL:-}"          # 若已有空仓库，直接填其 URL，跳过建库

cd "$(dirname "$0")"

if [ -z "$REMOTE_URL" ]; then
  echo "==> 创建 GitHub 仓库: $REPO_NAME ($VISIBILITY)"
  REMOTE_URL="$(gh repo create "$REPO_NAME" --"$VISIBILITY" --source=. --remote=origin --push 2>&1 | tail -1)"
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE_URL"
git branch -M main
git push -u origin main

echo "==> 完成: $REMOTE_URL"
