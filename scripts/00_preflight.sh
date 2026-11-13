#!/usr/bin/env bash
# 00_preflight.sh — 环境与静态层自检
# 用法: bash scripts/00_preflight.sh
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="$REPO/work/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/00_preflight.log"
: >"$LOG"
exec > >(tee -a "$LOG") 2>&1

FAIL=0
ok()   { printf '  [ OK ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  [WARN] %s\n' "$*"; }

echo "== 00_preflight =="
echo "REPO = $REPO"
echo

echo "-- 必需工具 --"
for t in python3 unzip zip curl jq java keytool zipalign apksigner patchelf apktool; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t -> $(command -v $t)"; else bad "$t 缺失"; fi
done
echo

echo "-- 可选工具（缺失不阻塞静态合包）--"
for t in dotnet mono ilspycmd; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else warn "$t 缺失（Interop 重生成需在 CI/PC 完成）"; fi
done
echo

echo "-- 仓库结构 --"
for d in config scripts static static/lemon static/lemon/deployment static/lemon/runtime \
         static/lemon/runtime/interop static/float static/libmain work dist; do
  if [ -d "$REPO/$d" ]; then ok "dir $d"; else warn "dir $d 不存在（构建时会创建）"; fi
done
echo

echo "-- 关键静态文件 --"
check_file() { # path, minsize
  f="$REPO/$1"
  if [ -f "$f" ]; then
    s=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$s" -ge "${2:-1}" ]; then ok "$1 ($(numfmt --to=iec "$s" 2>/dev/null || echo ${s}B))"; else bad "$1 过小 ($s)"; fi
  else bad "$1 缺失"; fi
}
check_file static/lemon/payload.json 100
check_file static/libmain/libmain.so 100000
check_file static/float/classes.dex 100000
check_file static/float/classes2.dex 100000
for so in libandroidrepublic.so libemtandroidrepublic.so libemtandroidrepublic2.so libxg4.so; do
  check_file "static/float/lib/arm64-v8a/$so" 1000
done
echo

echo "-- LemonLoader 部署层 --"
check_file static/lemon/deployment/Mods/TSKHook/TSKHook.dll 1000
check_file static/lemon/deployment/Mods/TSKHook/Utility.dll 100
check_file static/lemon/deployment/UserData/TSKHook/notosanscjktc 1000
check_file static/lemon/runtime/interop/Assembly-CSharp.dll 100000
echo "  interop 程序集数量: $(ls "$REPO/static/lemon/runtime/interop" 2>/dev/null | wc -l)"
echo

echo "-- 签名材料 --"
-- 签名材料 --
KS="$REPO/keystore/tsk_fixed.keystore"
if [ -f "$KS" ]; then
  ok "keystore 存在"
  # 安全：密码禁止硬编码入库，从环境变量 TSK_KS_PASS 读取；CI 用 secret 注入
  if [ -n "${TSK_KS_PASS:-}" ]; then
    keytool -list -alias tsk_fixed_key -store "$KS" -storepass "$TSK_KS_PASS" 2>/dev/null       | grep -i 'SHA-256' || warn "keystore 读取失败（密码/别名不匹配）"
  else
    warn "未设置 TSK_KS_PASS，跳过 keystore 指纹校验（签名阶段需通过 secret 提供密码）"
  fi
else
  warn "keystore 缺失：$KS （仅签名阶段需要）"
fi
echo

echo "-- 配置自检 --"
if python3 -c "import json,sys;json.load(open('$REPO/config/mod.json'))" 2>/dev/null; then
  ok "config/mod.json JSON 合法"
else
  bad "config/mod.json 解析失败"
fi
echo

echo "== 结果: $([ $FAIL -eq 0 ] && echo PASS || echo "$FAIL 项失败") =="
[ $FAIL -eq 0 ] || exit 1
