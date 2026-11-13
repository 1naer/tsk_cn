#!/usr/bin/env bash
# push_to_github.sh — 把本地仓库推送到 GitHub
#
# 用法（二选一）：
#   1) 推到 GitHub：先设置 GH_TOKEN，再运行本脚本
#   2) 推到 Gitee 镜像：设置 GITEE_REPO 后运行
#
# 安全：token 从环境变量读取，绝不写入仓库。

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# ---------- 方式一：GitHub ----------
if [ -n "${GH_TOKEN:-}" ] && [ -n "${GH_REPO:-}" ]; then
  echo "== 推送到 GitHub: $GH_REPO =="
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"
  git push -u origin master
  echo "[OK] GitHub 推送完成"
  exit 0
fi

# ---------- 方式二：Gitee 镜像（国内可直连）----------
if [ -n "${GITEE_REPO:-}" ]; then
  echo "== 推送到 Gitee: $GITEE_REPO =="
  git remote remove origin 2>/dev/null || true
  # Gitee 需要用户名+token，格式 https://<用户名>:<token>@gitee.com/<repo>.git
  git remote add origin "https://${GITEE_USER:-}:${GITEE_TOKEN:-}@gitee.com/${GITEE_REPO}.git"
  git push -u origin master
  echo "[OK] Gitee 推送完成"
  exit 0
fi

echo "未配置远程目标。请先设置环境变量："
echo "  GitHub:    GH_TOKEN=<你的token> GH_REPO=<用户名>/<仓库名> bash push_to_github.sh"
echo "  Gitee:     GITEE_USER=<用户名> GITEE_TOKEN=<token> GITEE_REPO=<用户名>/<仓库名> bash push_to_github.sh"
exit 2