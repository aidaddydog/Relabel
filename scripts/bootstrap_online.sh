#!/usr/bin/env bash
# Huandan 在线一键部署（交互稳健版：支持 bash <(curl ...) 执行；从 /dev/tty 读入）
# 说明：
# - 交互选择安装模式时，始终从 /dev/tty 读取，不依赖 STDIN 是否是 TTY
# - 非交互环境请用环境变量传参：INSTALL_MODE=fresh|upgrade 以及（fresh 时）ADMIN_USER/ADMIN_PASS
# - 全程中文日志；失败时给出一行日志调取命令

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

on_err(){
  local code=$?
  echo
  echo "✘ 安装失败（exit=$code）"
  echo "👉 一行日志命令：journalctl -u huandan.service -e -n 200"
  exit "$code"
}
trap on_err ERR

have_tty(){ [ -c /dev/tty ] && [ -r /dev/tty ]; }

tty_print(){ printf "%s" "$*" > /dev/tty; }
tty_read_line(){ # $1=varname $2=silent[yes|no] $3=prompt
  local __var="$1" __silent="${2:-no}" __prompt="${3:-}"
  if have_tty; then
    if [ -n "$__prompt" ]; then tty_print "$__prompt"; fi
    if [ "$__silent" = "yes" ]; then
      # 密码输入：关闭回显
      (stty -F /dev/tty -echo 2>/dev/null || stty -echo < /dev/tty)
      IFS= read -r __val < /dev/tty || true
      (stty -F /dev/tty echo 2>/dev/null || stty echo < /dev/tty)
      printf "\n" > /dev/tty
    else
      IFS= read -r __val < /dev/tty || true
    fi
    printf -v "$__var" "%s" "${__val:-}"
    return 0
  else
    return 1
  fi
}

ask(){ # ask "提示" VAR [yes|no silent]
  local prompt="$1" var="$2" silent="${3:-no}"
  if [ -n "${!var:-}" ]; then echo "$prompt（已由环境变量指定：$var）"; return 0; fi
  tty_read_line "$var" "$silent" "$prompt" || die "非交互环境：请通过环境变量提供 $var"
}

banner(){
  echo "============================================================"
  echo " Huandan Server 一键部署 | 目标目录：$DEST | 数据目录：$DATA"
  echo " 仓库：$REPO  分支：$BRANCH  端口：$PORT  HOST：$HOST"
  echo "============================================================"
}
banner

# —— 选择安装模式（循环直到有效） ——
if [ -z "${INSTALL_MODE:-}" ]; then
  if have_tty; then
    while :; do
      tty_print "请选择安装模式：\n  1) 全新安装（清空旧代码与数据）\n  2) 升级安装（保留数据与管理员，静默）\n输入数字 [1/2]："
      tty_read_line sel || sel=""
      case "${sel:-}" in
        1|fresh)   INSTALL_MODE="fresh";   break ;;
        2|upgrade) INSTALL_MODE="upgrade"; break ;;
        "" )       echo "⚠ 未输入，继续等待…";;
        * )        echo "✘ 无效选择：${sel}";;
      esac
    done
  else
    die "非交互环境：请设置 INSTALL_MODE=fresh|upgrade"
  fi
fi
ok "安装模式：$INSTALL_MODE"

# —— 全新安装：终端输入管理员账号/密码（隐藏回显 + 二次确认） ——
if [ "$INSTALL_MODE" = "fresh" ]; then
  ask "设置管理员用户名（默认 admin）：" ADMIN_USER
  ADMIN_USER="${ADMIN_USER:-admin}"
  while :; do
    ask "设置管理员密码：" ADMIN_PASS yes
    ask "再次输入管理员密码：" ADMIN_PASS2 yes
    if [ "${ADMIN_PASS}" != "${ADMIN_PASS2}" ]; then warn "两次输入不一致，请重试"; continue; fi
    if [ ${#ADMIN_PASS} -lt 12 ]; then warn "建议使用 12 位以上强口令（可继续）"; fi
    break
  done
else
  ok "升级安装：默认复用数据库现有管理员，不需输入口令"
fi

# —— 获取/更新代码并执行安装 —— 
step "下载/更新代码并执行安装（install_root.sh）"
export BRANCH REPO DEST DATA PORT HOST INSTALL_MODE ADMIN_USER ADMIN_PASS
bash -c 'set -Eeuo pipefail
  mkdir -p "$DEST"
  if [ -d "$DEST/.git" ]; then
    git -C "$DEST" fetch --all --prune || true
    git -C "$DEST" reset --hard "origin/$BRANCH" || true
  else
    git clone -b "$BRANCH" "$REPO" "$DEST"
  fi
  chmod +x "$DEST/scripts/install_root.sh"
  BASE="$DEST" bash "$DEST/scripts/install_root.sh"
'

echo
ok "完成。后台：http://<服务器IP>:$PORT/admin"
echo "（非交互示例）INSTALL_MODE=fresh ADMIN_USER=admin ADMIN_PASS='强口令' bash <(curl -fsSL $REPO/raw/$BRANCH/scripts/bootstrap_online.sh)"
echo "查看服务日志：journalctl -u huandan.service -e -n 200"
