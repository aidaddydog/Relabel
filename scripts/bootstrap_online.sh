#!/usr/bin/env bash
# Huandan 在线一键部署引导
# 用法：bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/huandan.server/main/scripts/bootstrap_online.sh)
set -Eeuo pipefail

LOG=/var/log/huandan-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

: "${BRANCH:=main}"
: "${REPO:=https://github.com/aidaddydog/huandan.server.git}"
: "${DEST:=/opt/huandan-server}"

step(){ echo "==> $*"; }
ok(){ echo "✔ $*"; }
die(){ echo "✘ $*"; exit 1; }
trap 'die "失败，详见 $LOG（或执行：journalctl -u huandan.service -e -n 200）"' ERR

[ "$(id -u)" -eq 0 ] || die "请用 root 运行"

step "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git curl ca-certificates tzdata python3-venv python3-pip ufw rsync unzip

step "获取代码到 $DEST（分支：$BRANCH）"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" fetch --all --prune || true
  git -C "$DEST" checkout "$BRANCH" || true
  git -C "$DEST" reset --hard "origin/$BRANCH" || true
  git -C "$DEST" clean -fd || true
else
  rm -rf "$DEST"
  git clone -b "$BRANCH" "$REPO" "$DEST"
fi
ok "代码准备完成"

step "准备 $DEST/.deploy.env（仓库内默认配置）"
if [ ! -f "$DEST/.deploy.env" ]; then
  cat > "$DEST/.deploy.env" <<'ENV'
PORT=8000
HOST=0.0.0.0
AUTO_CLEAN=no
BRANCH=main
REPO=https://github.com/aidaddydog/huandan.server.git
DATA=/opt/huandan-data
SECRET_KEY=please-change-me
# BASE 由安装脚本自动识别为当前仓库根
ENV
  ok "已写入 $DEST/.deploy.env"
else
  ok "$DEST/.deploy.env 已存在，保持不变"
fi

step "执行仓库安装脚本（非交互）"
cd "$DEST"
chmod +x scripts/install_root.sh
# 让安装脚本明确以 $DEST 作为 BASE，其他参数从 .deploy.env 读取
BASE="$DEST" bash scripts/install_root.sh

ok "完成。后台：http://<服务器IP>:8000/admin   首次初始化：/admin/bootstrap"
