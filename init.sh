#!/usr/bin/env bash
# init.sh · 一条命令自举：检查环境 → 装 dev 依赖 → 跑验证 → 打印下一步。
# 谁 clone 本仓库都能直接运行（Git Bash / WSL / macOS / Linux）。
set -euo pipefail

INSTALL_CMD="python -m pip install -q pytest"
VERIFY_CMD="python scripts/lint_marketplace.py --check && python scripts/sync_adapters.py --check && python -m pytest tests/ -q"

echo "== data-dev-skills skillhub 自举 =="

# 1) 环境检查
command -v git >/dev/null || { echo "[fail] 缺少 git"; exit 1; }
command -v python >/dev/null || { echo "[fail] 缺少 python（3.8+）"; exit 1; }
pyver=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[ok] git / python $pyver"

# 2) 安装开发依赖（已满足则跳过，pip 不可用但 pytest 已有也能继续）
if python -c "import pytest" 2>/dev/null; then
  echo "[ok] pytest 已可用，跳过安装"
else
  echo "[run] $INSTALL_CMD"
  $INSTALL_CMD
fi

# 3) 验证（失败即停）
echo "[run] lint --check + sync_adapters --check + pytest"
bash -c "$VERIFY_CMD"

# 4) 打印下一步
cat <<'EOF'

自举完成，验证全绿。接下来：
  开发：      读 AGENTS.md（根指令）+ progress.md（当前状态）+ feature_list.json（待办）
  接入平台：  读 docs/adapter.md，复制 adapter-configs/ 下最接近的一份配置
  订阅使用：  把 docs/install.md 的链接发给你的 agent（一句话安装，见 README）
EOF
