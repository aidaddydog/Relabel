#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Huandan Server 安装脚本（root）
# - 修复：调用 python -m app.admin_cli 前，确保 cd 到 BASE 且设置 PYTHONPATH=BASE
# - 生成 Pepper、写 systemd 环境、安装依赖、启动服务并初始化/复用管理员
# - 全程中文提示；失败给出一行日志命令
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---- 路径/配置 ----
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
on_err(){ local c=$?; echo; echo "✘ 安装失败（exit=$c）"; echo "👉 一行日志：journalctl -u huandan.service -e -n 200"; exit "$c"; }
trap on_err ERR

# 载入 .deploy.env 或设默认值（不覆盖外部已注入的同名变量）
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"
: "${BRANCH:=main}"
: "${REPO:=}"
: "${DATA:=/opt/huandan-data}"
: "${BASE:=$REPO_ROOT}"
: "${INSTALL_MODE:=upgrade}"   # fresh|upgrade
: "${ADMIN_USER:=admin}"
: "${ADMIN_PASS:=}"

# 0) 打印关键信息
step "环境"
echo "BASE=$BASE  DATA=$DATA  PORT=$PORT  HOST=$HOST  INSTALL_MODE=$INSTALL_MODE"

# 1) Python 运行环境
step "1) 安装 Python/依赖"
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y python3 python3-venv python3-pip git curl >/dev/null 2>&1 || true
python3 -V

# 2) 创建虚拟环境并安装依赖（含 Argon2）
step "2) 虚拟环境与依赖"
VENV="$BASE/.venv"
mkdir -p "$BASE" "$DATA" /etc/huandan
if [ ! -d "$VENV" ]; then python3 -m venv "$VENV"; fi
. "$VENV/bin/activate"
pip install --upgrade pip >/dev/null
# 关键：passlib[argon2] 提供 argon2-cffi；其余按项目需求可补
pip install -U "fastapi==0.114.*" "uvicorn[standard]==0.30.*" "passlib[bcrypt,argon2]" "sqlalchemy==2.*" "jinja2" "pandas" >/dev/null
ok "Python 依赖安装完成"

# 3) 生成 Pepper（若不存在）
PEPPER_FILE="/etc/huandan/secret_pepper"
step "3) 生成 Pepper"
if [ ! -s "$PEPPER_FILE" ]; then
  (umask 177; head -c 32 /dev/urandom > "$PEPPER_FILE")
  ok "Pepper 已生成：$PEPPER_FILE（权限 600）"
else
  ok "Pepper 已存在，保持不变"
fi
chmod 600 "$PEPPER_FILE" || true

# 4) 写 systemd 环境文件（服务运行所需）
ENV_SYS="/etc/huandan/huandan.env"
step "4) 写 EnvironmentFile：$ENV_SYS"
cat > "$ENV_SYS" <<ENV
HUANDAN_BASE="$BASE"
HUANDAN_DATA="$DATA"
HUANDAN_PEPPER_FILE="$PEPPER_FILE"
ENV
chmod 600 "$ENV_SYS"

# 5) 安装/刷新 systemd 服务
UNIT="/etc/systemd/system/huandan.service"
step "5) 安装/刷新 systemd 服务：$UNIT"
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
systemctl enable --now huandan.service

# 等待服务就绪（最多 30s）
for i in {1..30}; do
  sleep 1
  if curl -fsS "http://127.0.0.1:$PORT/admin/login" >/dev/null 2>&1; then
    ok "服务就绪"
    break
  fi
  [ "$i" -eq 30 ] && warn "登录页暂未返回 HTML（继续后续步骤，可稍后再查）"
done

# 6) 初始化/复用管理员（修复点：确保 cd 到 BASE 且设置 PYTHONPATH=BASE）
# -------------------------------------------------------------------
# 关键修复：python -m app.admin_cli 需要能找到 app 包
#   1) cd "$BASE"
#   2) 导出 PYTHONPATH="$BASE"
#   3) 同时导出 HUANDAN_* 环境（与运行时一致）
# -------------------------------------------------------------------
run_admin_cli() {
  local subcmd="$1"; shift
  (
    cd "$BASE" || exit 1
    export PYTHONPATH="$BASE"
    export HUANDAN_BASE="$BASE" HUANDAN_DATA="$DATA" HUANDAN_PEPPER_FILE="$PEPPER_FILE"
    exec "$VENV/bin/python" -m app.admin_cli "$subcmd" "$@"
  )
}

if [ "$INSTALL_MODE" = "fresh" ]; then
  step "6) 全新安装：初始化管理员（终端密码，不经 Web）"
  [ -n "$ADMIN_PASS" ] || die "未提供 ADMIN_PASS（应由 bootstrap_online.sh 交互收集）"
  if ! run_admin_cli init-admin -u "$ADMIN_USER" -p "$ADMIN_PASS"; then
    die "初始化管理员失败（app 包不可见或依赖错误？已设置 PYTHONPATH=$BASE）"
  fi
else
  step "6) 升级安装：复用现有管理员（若不存在则兜底初始化）"
  if run_admin_cli has-admin; then
    ok "已存在管理员，保持不变"
  else
    warn "数据库中未发现管理员，进入兜底初始化"
    [ -n "$ADMIN_PASS" ] || die "未提供 ADMIN_PASS（兜底初始化需要提供）"
    run_admin_cli init-admin -u "$ADMIN_USER" -p "$ADMIN_PASS"
    ok "兜底初始化管理员完成"
  fi
fi

# 7) 清理残留的 Web 初始化页（彻底关闭外部初始化入口）
step "7) 删除 Web 初始化页（若存在）"
rm -f "$BASE/app/templates/bootstrap.html" || true

# 8) 健康检查 & 提示
step "8) 健康检查"
curl -fsS "http://127.0.0.1:$PORT/admin/login" | head -n 1 >/dev/null \
  && ok "登录页可访问" \
  || warn "登录页未返回 HTML，可稍后查看服务日志"

echo
ok "部署完成 ✅  后台：http://<服务器IP>:$PORT/admin"
echo "查看日志：journalctl -u huandan.service -e -n 200"
echo "安装日志：$INSTALL_LOG"
