# 星骑士汉化合包仓库（Twinkle Star Knights / クルスタX）

自行维护《星骑士》IL2CPP 汉化 + ARTEAM 悬浮窗合包的完整流水线。
游戏更新后，一条命令即可产出新版汉化合包。

## 目录结构

```
.
├── scripts/              # 构建脚本（解包→注入→兼容性检查→打包→签名）
│   ├── 00_preflight.sh       环境与静态层自检
│   ├── 01_verify_input.py    APK 输入完整性校验
│   ├── 03_check_compat.py    IL2CPP 关键类型兼容性门禁（缺失即拒绝出包）
│   ├── inject.py             最小改动注入（manifest 权限 / onCreate 入口）
│   ├── zip_pack.py           直接 ZIP 打包（保留官方资源，绕开 apktool bug）
│   └── select_interop.py     Cpp2IL 生成结果安全筛选（按旧清单同名替换）
├── static/
│   ├── lemon/           LemonLoader 静态层（含旧版 interop 102 个 dll）
│   ├── float/           ARTEAM 悬浮窗（dex + native 库）
│   └── libmain/         LemonLoader 所需 libmain.so
├── config/mod.json      合包配置（兼容性门禁规则、注入规则、签名配置）
├── .github/workflows/   interop 自动生成 CI（需 .NET + Cpp2IL）
├── push_to_github.sh    推送脚本
└── .gitignore           排除 work/builds/keystore 等敏感与产物
```

## 快速开始

```bash
# 1. 准备官方 APK
export TSK_SOURCE_APK=/path/to/official.apk

# 2. 构建
bash scripts/00_preflight.sh          # 环境自检
bash scripts/01_verify_input.py       # 完整性校验
python3 scripts/03_check_compat.py    # 兼容性门禁
# ... 注入 + 打包 + 签名（完整流程见 docs/）
```

## ⚠️ 安全说明

- **签名密钥与密码不入库**：`keystore/`、`*.keystore` 已被 `.gitignore` 排除
- `00_preflight.sh` 中签名密码从环境变量 `TSK_KS_PASS` 读取，CI 用 secret 注入
- `work/`、`builds/`、`dist/` 构建产物均不入库

## 推送到远程（推荐交互式，免记忆变量）

```bash
bash push.sh
```

运行后脚本会用中文依次问你 3 个问题，照着回答即可：

1. **选择平台**：1=GitHub，2=Gitee（国内直连）
2. **仓库地址**：填 `用户名/仓库名`（不是完整 URL）
3. **访问令牌（Token）**：输入不显示，防泄露

脚本会自动处理推送，并在成功后清理 Token（不留在 .git/config 里）。

> 前置：在 GitHub/Gitee 的「设置 → 私人令牌」里生成一个 Token，
> 仓库权限需允许写入（Contents: Read and write）。

## 构建状态

- [x] 新版 APK 静态兼容性分析（8/8 类型命中）
- [x] 注入 + 静态层合并 + 直接 ZIP 打包 + zipalign + v3 签名
- [x] 首个候选合包 `tsk_v_new_cn_float_v1.apk` 已生成
- [ ] 真机安装与运行时验证（汉化 / 悬浮窗 / Interop）
- [ ] Cpp2IL 新版 Interop 生成（CI 待首次验证）