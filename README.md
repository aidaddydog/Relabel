
# Relabel Server

全新架构（FastAPI + React + PostgreSQL）的换单服务端，实现后台管理、三步导入、PDF ZIP 导入（SSE）、打印闭环、模板管理、在线更新等。

## 一键部署（在线）

将本仓库推送到 `https://github.com/aidaddydog/Relabel` 后，在目标服务器上执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/Relabel/main/scripts/bootstrap_online.sh)
```

可选参数（示例）：

```bash
REPO_URL=https://github.com/aidaddydog/Relabel.git \
RELABEL_BASE=/srv/relabel RELABEL_DATA=/srv/relabel/data PORT=8000 \
bash <(curl -fsSL https://raw.githubusercontent.com/aidaddydog/Relabel/main/scripts/bootstrap_online.sh)
```

## 本地开发（Docker）

```bash
cd docker/compose
docker compose up --build
# 打开 http://localhost:5173 (前端) 与 http://localhost:8000/healthz (后端)
```

## 裸机部署（已被 bootstrap 调用）

```bash
sudo bash scripts/install_root.sh -b /srv/relabel -d /srv/relabel/data -p 8000 -U admin -W 'admin123'
```

## 默认账号与访问码（开发）

- 管理员：`admin / admin123`
- 客户端访问码：`123456`
- 版本：默认 `1.97`（可在设置页修改）

## 目录结构

- `apps/server`：FastAPI 服务端、Alembic 迁移、脚本
- `apps/web`：React + Vite 前端
- `deploy/Relabel.service`：systemd 单元
- `scripts/`：一键安装、在线引导、备份、种子数据
- `docker/`：容器镜像与 Compose

## 关键接口
- 开放 API：`/api/v1/*`（version/mapping/pdf-zips/runtime/print/file/clients）
- 管理后台 API：`/admin/api/*`（orders/files/upload/reconcile/zips/settings）
- 管理页面：React SPA（`/admin/*`），由后端静态提供

## 注意
- `runtime/` 下需自行放置 `SumatraPDF-64.exe` / `SumatraPDF-32.exe`。
- `RELABEL_ENABLE_DANGEROUS=1` 才允许订单批量删除与对齐危险操作。
