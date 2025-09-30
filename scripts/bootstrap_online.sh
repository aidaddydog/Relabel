#!/usr/bin/env bash
# Huandan 在线一键部署（交互式：全新安装/升级安装 + 管理员初始化）
# 用法：bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/huandan.server/main/scripts/bootstrap_online.sh)
set -Eeuo pipefail

LOG=/var/log/huandan-bootstrap.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

# 可通过环境变量预设：INSTALL_MODE=fresh|upgrade ADMIN_USER=xxx ADMIN_PASS=xxx
: "${BRANCH:=main}"
: "${REPO:=https://github.com/aidaddydog/huandan.server.git}"
: "${DEST:=/opt/huandan-server}"
: "${DATA:=/opt/huandan-data}"
: "${PORT:=8000}"
: "${HOST:=0.0.0.0}"
: "${INSTALL_MODE:=}"
: "${ADMIN_USER:=}"
: "${ADMIN_PASS:=}"

step(){ echo "==> $*"; }
ok(){ echo "✔ $*"; }
warn(){ echo "⚠ $*"; }
die(){ echo "✘ $*"; exit 1; }
trap 'echo -e "\n✘ 失败，请执行：\n  journalctl -u huandan.service -e -n 200\n查看 $LOG 获取完整日志"; exit 1' ERR

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
if [ -z "$INSTALL_MODE" ]; then
  echo "请选择安装模式："
  echo "  1) 全新安装（清空旧代码与数据）"
  echo "  2) 升级安装（仅更新代码与依赖，不重置数据库）"
  while true; do
    read -r -p "输入数字 1 或 2 并回车: " ans
    case "$ans" in
      1) INSTALL_MODE="fresh"; break;;
      2) INSTALL_MODE="upgrade"; break;;
      *) echo "请输入 1 或 2";;
    esac
  done
fi
echo "安装模式：$INSTALL_MODE"

# 系统依赖
step "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends git curl ca-certificates tzdata python3-venv python3-pip ufw rsync unzip

# 获取/更新代码（为首次部署准备 install_root.sh）
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

# 创建/更新 .deploy.env（放在代码更新之后，避免被 git clean 删除）
step "准备 $DEST/.deploy.env（默认配置，不覆盖已有文件）"
mkdir -p "$DEST"
if [ ! -f "$DEST/.deploy.env" ]; then
  cat > "$DEST/.deploy.env" <<ENV
PORT=$PORT
HOST=$HOST
AUTO_CLEAN=no
BRANCH=$BRANCH
REPO=$REPO
DATA=$DATA
SECRET_KEY=please-change-me
# BASE 由安装脚本自动识别为当前仓库根
ENV
  ok "已生成 $DEST/.deploy.env"
else
  ok "$DEST/.deploy.env 已存在，保持不变"
fi

# 执行仓库内安装脚本（不要切换到 $DEST，避免被 AUTO_CLEAN 删除当前工作目录）
step "执行安装脚本"
chmod +x "$DEST/scripts/install_root.sh"
if [ "$INSTALL_MODE" = "fresh" ]; then
  AUTO_CLEAN=yes BASE="$DEST" bash "$DEST/scripts/install_root.sh"
else
  BASE="$DEST" bash "$DEST/scripts/install_root.sh"
fi

# 全新安装：管理员初始化（迁移网页步骤到脚本里）
if [ "$INSTALL_MODE" = "fresh" ]; then
  step "管理员初始化（脚本内完成）"
  ask "请输入管理员账号: " ADMIN_USER
  while [ -z "$ADMIN_USER" ]; do ask "管理员账号不能为空，请重新输入: " ADMIN_USER; done
  ask "请输入管理员密码（输入不可见）: " ADMIN_PASS yes
  while [ -z "$ADMIN_PASS" ]; do ask "管理员密码不能为空，请重新输入（输入不可见）: " ADMIN_PASS yes; done
  ask "请再次输入管理员密码确认: " ADMIN_PASS2 yes
  if [ "${ADMIN_PASS2:-}" != "$ADMIN_PASS" ]; then die "两次输入的密码不一致"; fi

  # 等待服务就绪
  echo "等待服务启动就绪..."
  for i in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$PORT/admin/bootstrap" -o /dev/null; then break; fi
    sleep 1
  done

  # 若系统已存在管理员，则跳过初始化
  HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/admin/bootstrap")" || true
  if [ "$HTTP_CODE" = "302" ]; then
    ok "系统检测到已存在管理员，跳过初始化"
  else
    # 提交初始化
    INIT_CODE="$(curl -s -o /dev/null -w "%{http_code}" -X POST       --data-urlencode "username=$ADMIN_USER"       --data-urlencode "password=$ADMIN_PASS"       "http://127.0.0.1:$PORT/admin/bootstrap")" || true

    if [ "$INIT_CODE" = "302" ]; then
      ok "管理员创建成功（已迁移到 /admin/login）"
    else
      warn "管理员创建未返回 302，HTTP=$INIT_CODE；请手动访问 /admin/bootstrap 或查看日志"
    fi
  fi
fi

echo
ok "完成。后台：http://<服务器IP>:$PORT/admin   首次初始化：已在脚本内完成（全新安装）"
echo "日志：journalctl -u huandan.service -e -n 200"
