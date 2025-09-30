#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P 2>/dev/null || pwd -P)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.deploy.env}"

TS="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="/var/log/huandan"
INSTALL_LOG="$LOG_DIR/install-root-$TS.log"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$INSTALL_LOG") 2>&1

die(){ echo "✘ $*" >&2; exit 1; }
warn(){ echo "⚠ $*"; }
ok(){ echo "✔ $*"; }
step(){ echo; echo "==> $*"; }

# 载入 .deploy.env 或设默认值
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi
: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"
: "${BRANCH:=main}"
: "${REPO:=}"
: "${DATA:=/opt/huandan-data}"
: "${BASE:=$REPO_ROOT}"
: "${INSTALL_MODE:=upgrade}"
: "${ADMIN_USER:=admin}"
: "${ADMIN_PASS:=}"

step "1) Python 运行环境"
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y python3 python3-venv python3-pip git curl >/dev/null 2>&1 || true
python3 -V

# venv
VENV="$BASE/.venv"
if [ ! -d "$VENV" ]; then python3 -m venv "$VENV"; fi
. "$VENV/bin/activate"
pip install --upgrade pip >/dev/null
pip install -U "fastapi==0.114.*" "uvicorn[standard]==0.30.*" "passlib[bcrypt,argon2]" "sqlalchemy==2.*" "jinja2" "pandas" >/dev/null

# 依赖目录
mkdir -p "$DATA" "$BASE/runtime" "$BASE/app/templates" "$BASE/static" /etc/huandan

# 生成 Pepper（若不存在）
PEPPER_FILE="/etc/huandan/secret_pepper"
if [ ! -s "$PEPPER_FILE" ]; then
  step "2) 生成 Pepper：$PEPPER_FILE"
  (umask 177; head -c 32 /dev/urandom > "$PEPPER_FILE")
  ok "Pepper 已生成（权限 600）"
else
  ok "Pepper 已存在，保持不变"
fi

# 写 service 环境变量文件
ENV_SYS="/etc/huandan/huandan.env"
step "3) 写环境文件：$ENV_SYS"
cat > "$ENV_SYS" <<ENV
HUANDAN_BASE="$BASE"
HUANDAN_DATA="$DATA"
HUANDAN_PEPPER_FILE="$PEPPER_FILE"
ENV
chmod 600 "$ENV_SYS"

# systemd 单元
UNIT=/etc/systemd/system/huandan.service
step "4) 安装/刷新 systemd 服务：$UNIT"
cat > "$UNIT" <<UNIT
[Unit]
Description=Huandan Server
After=network.target

[Service]
Type=simple
EnvironmentFile=$ENV_SYS
WorkingDirectory=$BASE
ExecStart=$VENV/bin/uvicorn app.main:app --host $HOST --port $PORT --workers 1 --proxy-headers
Restart=always
User=root
Group=root
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload

# 启动服务
step "5) 启动服务"
systemctl enable --now huandan.service

# 等待端口就绪
for i in {1..30}; do
  sleep 1
  curl -fsS "http://127.0.0.1:$PORT/admin/login" >/dev/null && break || true
done
ok "服务就绪"

# 初始化/复用管理员
if [ "$INSTALL_MODE" = "fresh" ]; then
  step "6) 全新安装：初始化管理员（终端密码，不经 Web）"
  if [ -z "$ADMIN_PASS" ]; then
    die "未提供 ADMIN_PASS（应由 bootstrap_online.sh 交互收集）"
  fi
  "$VENV/bin/python" -m app.admin_cli init-admin -u "$ADMIN_USER" -p "$ADMIN_PASS" || die "初始化管理员失败"
else
  step "6) 升级安装：复用现有管理员"
  "$VENV/bin/python" -m app.admin_cli has-admin && ok "已存在管理员，保持不变" || {
    warn "数据库中未发现管理员，进入兜底初始化"
    if [ -z "$ADMIN_PASS" ]; then
      die "未提供 ADMIN_PASS（兜底初始化需要提供）"
    fi
    "$VENV/bin/python" -m app.admin_cli init-admin -u "$ADMIN_USER" -p "$ADMIN_PASS" || die "初始化管理员失败"
  }
fi

# 清理 Web 初始化页（如残留）
rm -f "$BASE/app/templates/bootstrap.html"

# 健康检查
step "7) 健康检查"
curl -fsS "http://127.0.0.1:$PORT/admin/login" | head -n1 >/dev/null && ok "页面可访问" || warn "登录页未返回 HTML"

echo
ok "部署完成 ✅ 后台：http://<服务器IP>:$PORT/admin"
echo "日志：journalctl -u huandan.service -e -n 200"
echo "安装日志：$INSTALL_LOG"
