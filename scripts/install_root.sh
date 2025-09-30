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
step(){ echo "==> $*"; }
ok(){ echo "✔ $*"; }
warn(){ echo "⚠ $*"; }
die(){ echo "✘ $*"; exit 1; }
trap 'echo -e "✘ 安装失败（见日志：$INSTALL_LOG）\njournalctl -u huandan.service -e -n 200"; exit 1' ERR

# ---- 默认值（可被 .deploy.env/环境变量覆盖）----
REPO="${REPO:-}"; BRANCH="${BRANCH:-main}"
BASE="${BASE:-$REPO_ROOT}"
DATA="${DATA:-/opt/huandan-data}"
PORT="${PORT:-8000}"; HOST="${HOST:-0.0.0.0}"
PYBIN="${PYBIN:-python3}"
SECRET_KEY="${SECRET_KEY:-please-change-me}"
AUTO_CLEAN="${AUTO_CLEAN:-no}"

# 读取 .deploy.env
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
# 若在 git 仓库，自动取 origin
[ -z "${REPO:-}" ] && [ -d "$REPO_ROOT/.git" ] && REPO="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"

echo "BASE=$BASE DATA=$DATA PORT=$PORT HOST=$HOST REPO=${REPO:-<none>} BRANCH=$BRANCH"
[ "$(id -u)" -eq 0 ] || die "请用 root 运行"

# 1) 依赖
step "1) 安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git curl ca-certificates tzdata python3-venv python3-pip rsync unzip ufw
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
  git -C "$BASE" pull --ff-only || true
elif [ -n "${REPO:-}" ]; then
  if is_empty "$BASE"; then
    git clone -b "$BRANCH" "$REPO" "$BASE"
  else
    tmp="$(mktemp -d)"; git clone -b "$BRANCH" "$REPO" "$tmp"; rsync -a "$tmp/". "$BASE/"; rm -rf "$tmp"
  fi
else
  warn "未提供 REPO，假定 $BASE 已就位"
fi
ok "代码 OK"

# 5) Python 依赖
step "5) Python 依赖"
cd "$BASE"
$PYBIN -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip wheel
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  pip install 'uvicorn[standard]' fastapi jinja2 'sqlalchemy<2.0' 'passlib[bcrypt]' pandas openpyxl 'xlrd==1.2.0' aiofiles itsdangerous python-multipart
fi
ok "Python 依赖 OK"

# 6) /etc/default/huandan
step "6) 写入 /etc/default/huandan"
cat > /etc/default/huandan <<ENV
HUANDAN_BASE="$BASE"
HUANDAN_DATA="$DATA"
PORT="$PORT"
HOST="$HOST"
SECRET_KEY="$SECRET_KEY"
PYTHONUNBUFFERED=1
ENV
chmod 0644 /etc/default/huandan
ok "写入 /etc/default/huandan 完成"

# 7) systemd（绝对路径）
step "7) 写入 systemd 并启动（root）"
cat > /etc/systemd/system/huandan.service <<UNIT
[Unit]
Description=Huandan Server (FastAPI)
After=network.target

[Service]
EnvironmentFile=-/etc/default/huandan
WorkingDirectory=$BASE
ExecStart=$BASE/.venv/bin/python $BASE/run.py
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
UNIT

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
    Base.metadata.create_all(bind=engine, checkfirst=True)
except Exception as e:
    print("WARN create_all:", e)

db = SessionLocal()
set_mapping_version(db)
write_mapping_json(db)
print("OK: mapping.json rebuilt & version bumped")
PY
ok "映射文件重建完成"

# 9) UFW
step "9) 防火墙（若 UFW=active 且 HOST=0.0.0.0）"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active" && [ "$HOST" = "0.0.0.0" ]; then
  ufw allow "$PORT/tcp" || true
  ok "已放行 $PORT/tcp"
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
