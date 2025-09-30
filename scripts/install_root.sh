#!/usr/bin/env bash
set -Eeuo pipefail

# ---- 路径/配置 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P 2>/dev/null || pwd -P)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.deploy.env}"

TS="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="/var/log/huandan"
INSTALL_LOG="$LOG_DIR/install-root-$TS.log"
BACKUP_ROOT="/opt/huandan-backups"
BACKUP_DIR="$BACKUP_ROOT/$TS"

mkdir -p "$LOG_DIR" "$BACKUP_ROOT"
exec > >(tee -a "$INSTALL_LOG") 2>&1

info(){ echo "$*"; }
step(){ echo "==> $*"; }
ok(){ echo "✔ $*"; }
warn(){ echo "⚠ $*"; }
die(){ echo "✘ $*"; exit 1; }

# 读取环境
[ -f "$ENV_FILE" ] && source "$ENV_FILE" || true
: "${BASE:=${HUANDAN_BASE:-/opt/huandan-server}}"
: "${DATA:=${HUANDAN_DATA:-/opt/huandan-data}}"
: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"
: "${REPO:=https://github.com/aidaddydog/huandan.server.git}"
: "${BRANCH:=main}"
: "${AUTO_CLEAN:=no}"
: "${PYBIN:=${PYBIN:-python3}}"

info "BASE=$BASE DATA=$DATA PORT=$PORT HOST=$HOST REPO=$REPO BRANCH=$BRANCH"

# 1) 依赖
step "1) 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git curl ca-certificates tzdata python3 python3-pip rsync unzip ufw
# venv 优先尝试官方包，失败不阻断
apt-get install -y --no-install-recommends python3-venv || apt-get install -y --no-install-recommends python3.12-venv || true
ok "依赖安装完成"

# 2) 目录
step "2) 目录就绪（含 runtime/ updates/）"
install -d -m 755 "$BASE" "$BASE/runtime" "$BASE/updates" "$DATA/pdfs" "$DATA/uploads"
ok "目录 OK"

# 3) 备份策略
step "3) 备份策略：$AUTO_CLEAN"
if [ "$AUTO_CLEAN" = "yes" ] && [ -d "$BASE" ]; then
  systemctl stop huandan.service 2>/dev/null || true
  mkdir -p "$BACKUP_DIR"
  rsync -a --delete --exclude='.venv' "$BASE/" "$BACKUP_DIR/huandan-server/" 2>/dev/null || true
  rsync -a "$DATA/" "$BACKUP_DIR/huandan-data/" 2>/dev/null || true
  rm -rf "$BASE" && mkdir -p "$BASE" "$BASE/runtime" "$BASE/updates"
  ok "已备份到：$BACKUP_DIR，并覆盖安装"
else
  ok "就地更新（不清空目录）"
fi

# 4) 获取/更新代码
step "4) 获取/更新代码"
is_empty(){ [ -z "$(ls -A "$1" 2>/dev/null)" ]; }
if [ -d "$BASE/.git" ]; then
  git -C "$BASE" fetch --all --prune || true
  (git -C "$BASE" checkout "$BRANCH" 2>/dev/null || true)
  git -C "$BASE" reset --hard "origin/$BRANCH" || true
  git -C "$BASE" clean -fd || true
elif is_empty "$BASE"; then
  if [ -n "$REPO" ]; then
    git clone -b "$BRANCH" "$REPO" "$BASE"
  else
    warn "未提供 REPO，假定 $BASE 已就位"
  fi
else
  ok "代码 OK"
fi
ok "代码 OK"

# 5) Python 依赖
step "5) Python 依赖"
cd "$BASE"
# 创建虚拟环境：优先 python -m venv，失败回退 virtualenv（pip 或 apt）
if "$PYBIN" -m venv .venv 2>/dev/null; then
  :
else
  "$PYBIN" -m pip install -U virtualenv || apt-get install -y --no-install-recommends python3-virtualenv || true
  "$PYBIN" -m virtualenv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip wheel
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  # 如果没有 requirements.txt，按需安装最小集
  pip install 'uvicorn[standard]' fastapi jinja2 'sqlalchemy<2.0' aiosqlite openpyxl 'xlrd==1.2.0' aiofiles itsdangerous python-multipart
fi
ok "Python 依赖 OK"

# 6) /etc/default/huandan
step "6) 写入 /etc/default/huandan"
cat > /etc/default/huandan <<ENV
HUANDAN_BASE="$BASE"
HUANDAN_DATA="$DATA"
PORT="$PORT"
HOST="$HOST"
ENV
ok "环境文件已写入"

# 7) systemd 单元
step "7) 写入 systemd 服务并启动"
install -D -m 0644 "$REPO_ROOT/deploy/huandan.service" /etc/systemd/system/huandan.service
systemctl daemon-reload
systemctl enable --now huandan.service || true
systemctl --no-pager -l status huandan.service | sed -n '1,60p'

# 8) 重建 mapping.json（避免导入时并发建表）
step "8) 重建 mapping.json（修正 sys.path + 先确保建表）"
mkdir -p "$BASE/runtime" "$BASE/updates"
env BASE="$BASE" HUANDAN_DATA="$DATA" "$BASE/.venv/bin/python" - <<'PY'
import os, sys
base = os.environ['BASE']
sys.path.insert(0, base)

# 先确保表存在（避免服务启动与此处并发导致的 no such table）
from app.main import Base, engine, SessionLocal, write_mapping_json, set_mapping_version
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print("create_all warning:", e)

# 写入 mapping.json 及版本号
try:
    write_mapping_json()
    set_mapping_version()
    print("mapping.json rebuilt")
except Exception as e:
    print("mapping.json rebuild warning:", e)
PY

# 9) 防火墙
step "9) 防火墙端口"
if command -v ufw >/dev/null && [ "${HOST}" = "0.0.0.0" ]; then
  ufw allow "${PORT}/tcp" || true
  ok "已放行端口 ${PORT}/tcp"
else
  warn "UFW 未启用或 HOST 非 0.0.0.0，跳过"
fi

# 10) 健康检查
step "10) 健康检查"
sleep 1
curl -fsS "http://127.0.0.1:$PORT/admin/login" | head -n 1 >/dev/null && echo "OK - 本机可访问" || warn "未返回 HTML，查看日志"

echo
ok "部署完成 ✅"
echo "后台：http://<服务器IP>:$PORT/admin   首次：/admin/bootstrap"
echo "日志：journalctl -u huandan.service -e -n 200"
echo "安装日志：$INSTALL_LOG"
