#!/usr/bin/env bash
# Huandan 在线一键部署（交互稳健版：支持 bash <(curl ...) 运行）
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

have_tty(){ [ -r /dev/tty ]; }

tty_read(){ # $1=prompt  => echo to tty, read from tty into REPLY
  local _p="$1"
  if have_tty; then
    printf "%s" "$_p" > /dev/tty
    IFS= read -r REPLY < /dev/tty
    printf "\n" > /dev/tty
    return 0
  else
    return 1
  fi
}

ask(){ # ask "提示" VAR [yes|no silent]
  local prompt="$1"; local var="$2"; local silent="${3:-no}"; local val=""
  if [ -n "${!var:-}" ]; then echo "$prompt（已由环境变量指定）"; return 0; fi
  if have_tty; then
    if [ "$silent" = "yes" ]; then
      printf "%s" "$prompt" > /dev/tty
      stty -echo < /dev/tty
      IFS= read -r val < /dev/tty
      stty echo < /dev/tty
      printf "\n" > /dev/tty
    else
      printf "%s" "$prompt" > /dev/tty
      IFS= read -r val < /dev/tty
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
  if have_tty && tty_read "输入数字 [1/2]："; then
    sel="$REPLY"
  else
    die "非交互环境：请通过环境变量指定 INSTALL_MODE=fresh|upgrade"
  fi
  case "$sel" in
    1) INSTALL_MODE="fresh" ;;
    2) INSTALL_MODE="upgrade" ;;
    fresh|upgrade) INSTALL_MODE="$sel" ;;
    *) die "无效选择：$sel" ;;
  esac
fi
ok "安装模式：$INSTALL_MODE"

# 全新安装需要管理员信息
if [ "$INSTALL_MODE" = "fresh" ]; then
  ask "设置管理员用户名（默认 admin）：" ADMIN_USER
  ADMIN_USER="${ADMIN_USER:-admin}"
  while :; do
    ask "设置管理员密码：" ADMIN_PASS yes
    ask "再次输入管理员密码：" ADMIN_PASS2 yes
    [ "$ADMIN_PASS" = "$ADMIN_PASS2" ] || { warn "两次输入不一致，请重试"; continue; }
    if [ ${#ADMIN_PASS} -lt 12 ]; then warn "建议使用 12 位以上强口令"; fi
    break
  done
else
  ok "升级安装：默认复用数据库现有管理员，不需输入口令"
fi

# 拉取/更新代码并执行安装
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
echo "非交互用法示例：INSTALL_MODE=fresh ADMIN_USER=admin ADMIN_PASS='你的复杂口令' bash <(curl -fsSL $REPO/raw/$BRANCH/scripts/bootstrap_online.sh)"
echo "日志：journalctl -u huandan.service -e -n 200"
