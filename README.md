# Huandan Server（从 0 开始一键到线上）

> 适配系统：**Ubuntu 24.04 LTS**（其他 Debian/Ubuntu 也大同小异）  
> 技术栈：FastAPI + SQLite + Jinja2 + Uvicorn  
> 运行账户：推荐使用**非 root 专用用户 `huandan`**  
> 目录约定：代码 `/opt/huandan-server`，数据 `/opt/huandan-data`（PDF/上传分离，便于备份）

---

## 一、功能与入口

- 后台地址：`http://<服务器IP>:${PORT}/admin`
- 首次初始化管理员：使用 CLI `python -m app.admin_cli init-admin -u <用户名> -p <强口令>`
- 数据目录：`${HUANDAN_DATA}`（默认 `/opt/huandan-data`，含 `pdfs/` 与 `uploads/`）
- 主要 API：
  - `GET /api/v1/version?code=xxxxxx`
  - `GET /api/v1/mapping?code=xxxxxx`
  - ...

### 初始化管理员（CLI）

```bash
cd /opt/huandan-server
python3 -m app.admin_cli init-admin -u admin -p '强口令'
