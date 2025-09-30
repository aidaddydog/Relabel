#!/usr/bin/env bash
# Huandan 在线一键部署（交互：全新安装/升级安装；删除Web初始化页；终端创建/复用管理员）
set -Eeuo pipefail

LOG=/var/log/huandan-bootstrap.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

: "${BRANCH:=main}"
: "${REPO:=https://github.com/aidaddydog/huandan.server.git}"
: "${DEST:=/opt/huandan-server}"
: "${DATA:=/opt/huandan-data}"
: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"

die(){ echo "✘ $*" >&2; exit 1; }
warn(){ echo "⚠ $*"; }
ok(){ echo "✔ $*"; }
step(){ echo; echo "==> $*"; }

is_tty(){ [ -t 0 ] && [ -t 1 ]; }
ask() {
  local prompt="$1"; local var="$2"; local silent="${3:-no}"; local val=""
  if [ -n "${!var:-}" ]; then echo "$prompt（已由环境变量指定）"; return 0; fi
  if is_tty; then
    if [ "$silent" = "yes" ]; then
      read -r -s -p "$prompt" val; echo
    else
      read -r -p "$prompt" val
    fi
    printf -v "$var" "%s" "$val"
  else
    die "非交互环境且未提供 $var"
  fi
}

banner(){
  echo "============================================================"
  echo " Huandan Server 一键部署 | 目标目录：$DEST | 数据目录：$DATA"
  echo " 仓库：$REPO  分支：$BRANCH  端口：$PORT  HOST：$HOST"
  echo "============================================================"
}

banner

# 选择安装模式
if [ -z "${INSTALL_MODE:-}" ]; then
  echo "请选择安装模式："
  echo "  1) 全新安装（清空旧代码与数据）"
  echo "  2) 升级安装（保留数据与管理员，静默）"
  read -r -p "输入数字 [1/2]：" sel
  case "$sel" in
    1) INSTALL_MODE="fresh" ;;
    2) INSTALL_MODE="upgrade" ;;
    *) die "无效选择" ;;
  esac
fi
ok "安装模式：$INSTALL_MODE"

# 如为全新安装，收集管理员账号/密码（隐藏回显/二次确认）
if [ "$INSTALL_MODE" = "fresh" ]; then
  ask "设置管理员用户名（默认 admin）：" ADMIN_USER
  ADMIN_USER="${ADMIN_USER:-admin}"
  while :; do
    ask "设置管理员密码：" ADMIN_PASS yes
    ask "再次输入管理员密码：" ADMIN_PASS2 yes
    [ "$ADMIN_PASS" = "$ADMIN_PASS2" ] || { warn "两次输入不一致，请重试"; continue; }
    # 简单强度门槛
    if [ ${#ADMIN_PASS} -lt 12 ]; then
      warn "建议使用 12 位以上强口令"; fi
    break
  done
else
  ok "升级安装：默认复用数据库现有管理员，不需输入口令"
fi

# 拉取/更新代码，执行安装脚本
step "下载/更新代码并执行安装（install_root.sh）"
export BRANCH REPO DEST DATA PORT HOST INSTALL_MODE ADMIN_USER ADMIN_PASS
bash -c 'set -Eeuo pipefail; 
  mkdir -p "$DEST";
  if [ -d "$DEST/.git" ]; then
    git -C "$DEST" fetch --all --prune || true
    git -C "$DEST" reset --hard "origin/$BRANCH" || true
  else
    git clone -b "$BRANCH" "$REPO" "$DEST"
  fi
  chmod +x "$DEST/scripts/install_root.sh"
  BASE="$DEST" bash "$DEST/scripts/install_root.sh"
'

ok "完成。后台：http://<服务器IP>:$PORT/admin"
echo "日志：journalctl -u huandan.service -e -n 200"
