#!/usr/bin/env bash
# push.sh — 星骑士汉化合包仓库 · 交互式一键推送
#
# 设计目的：彻底消除"要设哪些变量"的负担。
# 运行后脚本会用中文依次问你 3 个问题，照着回答即可，无需记忆任何变量名。
#
# 用法：
#   cd tsk-cn-mod
#   bash push.sh

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

echo "=================================================="
echo "  星骑士汉化合包仓库 · 一键推送"
echo "=================================================="
echo
echo "  推送平台："
echo "    1) GitHub"
echo "    2) Gitee（国内直连，备选）"
read -rp "  请选择 [1/2]: " choice

case "$choice" in
  1) HOST="github.com"; PLAT="GitHub" ;;
  2) HOST="gitee.com";  PLAT="Gitee" ;;
  *) echo "  无效选择，退出"; exit 1 ;;
esac

# 问题 1：仓库地址
repo=""
while [ -z "$repo" ]; do
  read -rp "  仓库地址（格式：用户名/仓库名）: " repo
  case "$repo" in
    */*) ;;
    *) echo "    格式不对，应包含一个 /，例如：你的名字/tsk-cn-mod" ;;
  esac
done

# 问题 2：Token（静默输入，不回显，防泄露）
token=""
while [ -z "$token" ]; do
  read -rsp "  输入 $PLAT 访问令牌（Token，输入不显示）: " token
  echo
  if [ -z "$token" ]; then echo "    Token 不能为空，请重新输入"; fi
done

# 从仓库地址拆出用户名（Gitee 需要，GitHub 用 x-access-token 占位）
username="${repo%%/*}"

echo
echo "=================================================="
echo "  即将推送到：$PLAT   $repo"
echo "=================================================="
echo
echo "  正在推送，请稍候..."

git remote remove origin 2>/dev/null || true

case "$PLAT" in
  GitHub) URL="https://x-access-token:${token}@${HOST}/${repo}.git" ;;
  Gitee)  URL="https://${username}:${token}@${HOST}/${repo}.git" ;;
esac
git remote add origin "$URL"

if git push -u origin master 2>&1; then
  echo
  echo "✅ 推送成功！仓库已在远程就位。"
  # 安全收尾：把带 Token 的 remote 改回安全地址，Token 不留在仓库配置里
  git remote set-url origin "https://${HOST}/${repo}.git"
  echo "🔒 已自动清理，Token 不会留在 .git/config 里。"
else
  echo
  echo "❌ 推送失败，常见原因："
  echo "   - Token 无效或已过期（去 $PLAT 的 设置→令牌 重新生成）"
  echo "   - 仓库地址填错（注意是 用户名/仓库名，不是完整 URL）"
  echo "   - 网络不通（GitHub 连不上就重新运行，选 2 改用 Gitee）"
  exit 1
fi