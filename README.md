# Huandan Server（从 0 开始一键到线上）

> 适配系统：**Ubuntu 24.04 LTS**（其他 Debian/Ubuntu 也大同小异）  
> 技术栈：FastAPI + SQLite + Jinja2 + Uvicorn  
> 运行账户：推荐使用**非 root 专用用户 `huandan`**  
> 目录约定：代码 `/opt/huandan-server`，数据 `/opt/huandan-data`（PDF/上传分离，便于备份）

---

## 一、功能与入口

- 后台地址：`http://<服务器IP>:${PORT}/admin`
- 首次初始化管理员：`/admin/bootstrap`
- 数据目录：`${HUANDAN_DATA}`（默认 `/opt/huandan-data`，含 `pdfs/` 与 `uploads/`）
- 主要 API：
  - `GET /api/v1/version?code=xxxxxx`
  - `GET /api/v1/mapping?code=xxxxxx`
  - `GET /api/v1/file/{tracking_no}?code=xxxxxx`
  - `GET /api/v1/runtime/sumatra?arch=win64&code=xxxxxx`（分发运行时安装包，需将文件放到 `runtime/`）

> ⚠️ 出于安全考虑，「清空全部 PDF/订单」的**危险端点默认未启用**。如确需，请单独向我索取“注入脚本”。

---

## 二、从 0 开始部署（完整指令，复制分步执行）

> **目标**：新服务器 → 安装 Git / Python / UFW → 创建专用用户 → 拉取仓库 → 配置虚拟环境 → 以 systemd 常驻。

### Step 0. 切到 root（如已是 root 可跳过）

```bash
sudo -i
```

### Step 1. 基础环境 & 常用工具

```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  git curl ca-certificates tzdata openssh-server \
  python3-venv python3-pip rsync unzip \
  ufw
timedatectl set-timezone Asia/Shanghai
```

### Step 2. 创建专用用户与目录（推荐非 root 运行）

```bash
# 2.1 创建专用用户（无密码登录，仅供运行服务；如需密码可自行设置）
adduser --disabled-password --gecos "" huandan || true

# 2.2 预创建目录并授予权限（代码与数据分离）
install -d -m 755 /opt/huandan-server /opt/huandan-data
chown -R huandan:huandan /opt/huandan-server /opt/huandan-data
```

### Step 3. （可选）配置 UFW 防火墙（**先放行 SSH 再启用**）

> 若你的机器已在内网或有其他防火墙，可跳过此节。

```bash
ufw allow OpenSSH
ufw allow 8000/tcp      # 如果直接裸跑在 8000 端口
# 若后续走 Nginx 反代，可改为放行 80/443：
# ufw allow 80/tcp && ufw allow 443/tcp
ufw enable
ufw status verbose
```

### Step 4. 以 **SSH Key** 配置 Git 并拉取仓库（推荐）

> 以下操作**在 `huandan` 用户下**进行（确保仓库可写入 `/opt/huandan-server`）。

```bash
# 4.1 切到专用用户
sudo -u huandan -H bash -lc '
  set -Eeuo pipefail
  # 生成 SSH Key（无口令）
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  ssh-keygen -t ed25519 -C "huandan@$(hostname)" -N "" -f ~/.ssh/id_ed25519
  echo "===== 将下面的公钥添加到你的 Git 平台（GitHub/Gitee）的 SSH Keys 中 ====="
  cat ~/.ssh/id_ed25519.pub
  echo "===================================================================="
  echo "添加完成后按回车继续..."; read _

  # 避免首次连接交互（任选其一 / 或都加上）
  ssh-keyscan -H github.com  >> ~/.ssh/known_hosts 2>/dev/null || true
  ssh-keyscan -H gitee.com   >> ~/.ssh/known_hosts 2>/dev/null || true

  # 拉取仓库到指定目录（替换为你的仓库地址）
  GIT_SSH_URL="git@github.com:<你的组织或用户名>/huandan-server.git"
  # 若仓库不是此名，请改成你的实际仓库名
  git clone "$GIT_SSH_URL" /opt/huandan-server
'
```

> 如果你更习惯 **HTTPS + Token**：  
> `git clone https://<token>@github.com/<owner>/<repo>.git /opt/huandan-server`

### Step 5. 创建 Python 虚拟环境并安装依赖

```bash
sudo -u huandan -H bash -lc '
  cd /opt/huandan-server
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -U pip wheel
  pip install -r requirements.txt
'
```

### Step 6. 准备数据目录（PDF 与上传）

```bash
sudo -u huandan -H bash -lc '
  export HUANDAN_DATA=/opt/huandan-data
  mkdir -p $HUANDAN_DATA/pdfs $HUANDAN_DATA/uploads
'
```

### Step 7. 先试跑（前台运行，确认无报错后 Ctrl+C 退出）

```bash
sudo -u huandan -H bash -lc '
  cd /opt/huandan-server
  export HUANDAN_BASE=/opt/huandan-server
  export HUANDAN_DATA=/opt/huandan-data
  PORT=8000 HOST=0.0.0.0 SECRET_KEY=please-change-me \
    .venv/bin/python run.py
'
```

现在浏览器访问：`http://<服务器IP>:8000/admin/bootstrap`，完成**管理员初始化**。

### Step 8. 配置 systemd 常驻

> 推荐使用随仓库提供的单元模板为蓝本，不过我们在这里**直接生成并覆盖**（以 `huandan` 用户运行）。

```bash
cat >/etc/systemd/system/huandan.service <<'UNIT'
[Unit]
Description=Huandan Server (FastAPI)
After=network.target

[Service]
Environment=HUANDAN_BASE=/opt/huandan-server
Environment=HUANDAN_DATA=/opt/huandan-data
Environment=PORT=8000
Environment=HOST=0.0.0.0
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=/opt/huandan-server
ExecStart=/opt/huandan-server/.venv/bin/python /opt/huandan-server/run.py
Restart=always
User=huandan
Group=huandan

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now huandan.service
systemctl --no-pager -l status huandan.service | sed -n '1,80p'
```

### Step 9. 常用排错与验证

```bash
# 查看服务日志（最近 200 行）
journalctl -u huandan.service -e -n 200

# 端口监听检查（应出现 :8000）
ss -lntp | grep :8000 || true

# 本机连通性烟囱测试（应返回 HTML）
curl -sSf http://127.0.0.1:8000/admin/login | head -n 20
```

---

## 三、日常运维

### 升级部署（Git 拉取 → 依赖同步 → 平滑重启）

```bash
sudo -u huandan -H bash -lc '
  cd /opt/huandan-server
  git pull
  source .venv/bin/activate
  pip install -r requirements.txt
'
sudo systemctl restart huandan.service
journalctl -u huandan.service -e -n 100
```

### 备份与回滚

```bash
# 备份（打包代码与数据目录）
sudo bash /opt/huandan-server/scripts/backup.sh /opt/huandan-backups/backup-$(date +%F-%H%M).tar.gz

# 回滚示例（将某个备份还原回 / ）
# 警告：会覆盖目标路径，请先在测试机验证
# sudo tar -xzf /opt/huandan-backups/<你的备份>.tar.gz -C /
```

### 防火墙规则调整

- 裸跑端口：`ufw allow 8000/tcp`  
- 反代端口：`ufw allow 80/tcp && ufw allow 443/tcp`  
- 查看状态：`ufw status verbose`

---

## 四、（可选）通过 Nginx 反代并启用 HTTPS

> 你可以用 1Panel 可视化配置；或在命令行：

**Nginx 站点示例**（反代到本机 8000）

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
```

> 启用 HTTPS 建议配合 `certbot` 或 1Panel 的证书管理。放行 80/443 后再申请证书。

---

## 五、环境变量清单

- `PORT`（默认 8000）
- `HOST`（默认 0.0.0.0）
- `HUANDAN_BASE`（自动推断为仓库根）
- `HUANDAN_DATA`（默认 `/opt/huandan-data`）
- `SECRET_KEY`（会话密钥，建议修改为随机值）

---

## 六、目录结构

```
/opt/huandan-server
├─ README.md
├─ requirements.txt
├─ .env.example
├─ .gitignore
├─ run.py
├─ deploy/
│  └─ huandan.service
├─ scripts/
│  └─ backup.sh
├─ app/
│  ├─ __init__.py
│  ├─ main.py
│  ├─ templates/…
│  └─ static/style.css
├─ updates/ (空占位)
├─ runtime/ (空占位)
└─ tests/   (空占位)
/opt/huandan-data
├─ pdfs/
└─ uploads/
```

---

## 七、FAQ

1. **首次访问 8000 端口失败？**  
   - 检查：`journalctl -u huandan.service -e -n 200`  
   - 防火墙：`ufw status verbose`，确认是否放行 `8000/tcp` 或 `80/443`。  
   - 端口占用：`ss -lntp | grep :8000`。

2. **如何更换运行端口？**  
   - 修改 `/etc/systemd/system/huandan.service` 的 `Environment=PORT=xxxx`，然后：  
     `systemctl daemon-reload && systemctl restart huandan.service`。

3. **SQLite 文件在哪里？**  
   - 在代码目录根：`/opt/huandan-server/huandan.sqlite3`（跟随仓库路径）。

4. **危险端点如何启用？**  
   - 默认不包含。需要时联系我获取“注入脚本”，脚本会加入带确认短语的高危表单与接口。

—— 完 ——
