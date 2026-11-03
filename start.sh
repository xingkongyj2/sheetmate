#!/usr/bin/env bash
# SheetMate：释放前后端端口 → 安装依赖 → 构建前端 → 启动后端
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"

trap 'echo "启动失败（第 $LINENO 行），请检查上方错误。" >&2' ERR

echo "=== SheetMate ==="

# 1. 配置与端口。只读取 PORT，不将含有密钥的 .env 当成 shell 脚本执行。
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env：填上 DEEPSEEK_API_KEY 即可使用（也可以启动后在页面「设置」里填）"
fi

read_env_port() {
  [ -f "$1" ] || return 1
  awk '
    /^[[:space:]]*(export[[:space:]]+)?PORT[[:space:]]*=/ {
      sub(/^[^=]*=[[:space:]]*/, "")
      sub(/\r$/, "")
      if ($0 ~ /^"/) { sub(/^"/, ""); sub(/".*/, "") }
      else if ($0 ~ /^\047/) { sub(/^\047/, ""); sub(/\047.*/, "") }
      else { sub(/(^|[[:space:]]+)#.*/, ""); sub(/[[:space:]]+$/, "") }
      port = $0; found = 1
    }
    END { if (found) print port; else exit 1 }
  ' "$1"
}

if [ "${PORT+x}" = x ]; then
  PORT="${PORT:-8000}"
elif configured_port="$(read_env_port "$ROOT/.env")"; then
  PORT="${configured_port:-8000}"
elif configured_port="$(read_env_port "$ROOT/backend/.env")"; then
  PORT="${configured_port:-8000}"
else
  PORT=8000
fi
if ! PORT="$(awk -v port="$PORT" 'BEGIN {
  gsub(/^[[:space:]]+|[[:space:]]+$/, "", port)
  if (port == "") port = 8000
  number = port + 0
  if (port !~ /^[0-9]+$/ || number < 1 || number > 65535) exit 1
  printf "%d", number
}')"; then
  echo "PORT 必须是 1 到 65535 之间的整数，请检查配置。" >&2
  exit 1
fi
export PORT

command -v lsof >/dev/null 2>&1 || { echo "请先安装 lsof，用于释放启动端口。" >&2; exit 1; }
free_port() {
  local port="$1" pids pid attempt
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "结束占用端口 $port 的进程：$(echo "$pids" | tr '\n' ' ')"
    for pid in $pids; do kill -KILL "$pid" 2>/dev/null || true; done
  fi
  for attempt in {1..30}; do
    if ! lsof -nP -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "端口 $port 已释放"
      return 0
    fi
    sleep 0.1
  done
  echo "无法释放端口 $port，请检查进程权限。" >&2
  return 1
}
ports_ready=true
free_port "$PORT" || ports_ready=false
free_port 5173 || ports_ready=false
[ "$ports_ready" = true ] || exit 1

# 2. 后端依赖；运行始终使用 backend/.venv。
command -v node >/dev/null 2>&1 || { echo "请先安装 Node.js 20+。" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "未找到 npm，请检查 Node.js 安装。" >&2; exit 1; }
cd "$ROOT/backend"
if [ ! -x "$PYTHON" ]; then
  echo "创建 Python 虚拟环境…"
  # 需要 Python 3.10 及以上
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv -q
  else
    command -v python3 >/dev/null 2>&1 || { echo "请先安装 Python 3.10+ 或 uv。" >&2; exit 1; }
    python3 -m venv .venv
  fi
fi
echo "安装后端依赖…"
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python "$PYTHON" -r requirements.txt
else
  "$PYTHON" -m pip install -q -r requirements.txt
fi

# 3. 同步前端依赖并重新构建，确保配置和依赖修改也生效。
cd "$ROOT/frontend"
echo "安装前端依赖…"
npm install --no-audit --no-fund
echo "构建前端…"
npm run build

# 4. 启动
cd "$ROOT/backend"
echo ""
echo "打开 http://127.0.0.1:$PORT   默认管理员 admin / admin123   （Ctrl+C 停止）"
echo ""
exec "$PYTHON" -m sheetmate
