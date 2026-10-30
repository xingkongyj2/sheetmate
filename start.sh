#!/usr/bin/env bash
# SheetMate 一键启动（单端口）：安装依赖 → 构建前端 → 启动后端，浏览器打开 http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

echo "=== SheetMate ==="

# 1. 配置文件
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env：填上 DEEPSEEK_API_KEY 即可使用（也可以启动后在页面「设置」里填）"
fi

# 2. 后端依赖（优先用 uv，没有就用 python3 -m venv）
cd "$ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  echo "创建 Python 虚拟环境…"
  # 需要 Python 3.10 及以上
  if command -v uv >/dev/null 2>&1; then uv venv .venv -q; else python3 -m venv .venv; fi
fi
echo "安装后端依赖…"
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install -q -r requirements.txt
fi

# 3. 前端构建（源码有变化时才重新构建）
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then
  echo "安装前端依赖…"
  npm install --no-audit --no-fund
fi
if [ ! -f dist/index.html ] || [ -n "$(find src index.html -newer dist/index.html 2>/dev/null | head -1)" ]; then
  echo "构建前端…"
  npm run build
fi

# 4. 启动
cd "$ROOT/backend"
PORT="${PORT:-$(grep -E '^PORT=' "$ROOT/.env" | cut -d= -f2 || true)}"
echo ""
echo "打开 http://127.0.0.1:${PORT:-8000}   默认管理员 admin / admin123   （Ctrl+C 停止）"
echo ""
exec .venv/bin/python -m sheetmate
