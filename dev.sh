#!/usr/bin/env bash
# 开发模式：后端 :8000（改代码后手动重启）+ 前端 Vite :5173（热更新，/api 自动代理到后端）
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
[ -f .env ] || cp .env.example .env

cd "$ROOT/backend"
[ -x .venv/bin/python ] || { echo "先运行一次 ./start.sh 安装依赖"; exit 1; }
.venv/bin/python -m uvicorn sheetmate.app:app --host 127.0.0.1 --port 8000 --reload &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT

cd "$ROOT/frontend"
[ -d node_modules ] || npm install --no-audit --no-fund
echo "打开 http://localhost:5173"
npm run dev
