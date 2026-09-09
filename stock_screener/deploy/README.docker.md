# Docker Compose 部署

此目录的 `docker-compose.yml` 为 Docker Compose 准备，服务拓扑与
`podman-compose.yml` 一致：Caddy、前端构建、Web API、后台 Worker、MySQL 和
Redis。应用镜像复用 `Containerfile`；Docker 将它作为 `dockerfile` 正常构建，
无需维护重复的 Dockerfile。

## 前置条件

- Docker Engine 及 Docker Compose plugin。
- 宿主机开放 TCP 80、443；不要向公网开放 MySQL 3306 或 Redis 6379。
- 已准备 `stock_screener/.env`，可从 `.env.example` 复制，并设置强密码、
  `AGENT_TOKEN` 与 Web 登录凭据。

在项目根目录创建持久化目录并配置权限：

```bash
sudo mkdir -p /data/moneymanager/{mysql,redis,artifacts}
sudo chown -R "$USER":"$USER" /data/moneymanager
```

## 启动与维护

```bash
cd /opt/MoneyManager/stock_screener
cp .env.example .env  # 首次执行；之后编辑 .env
chmod 600 .env
cd deploy
docker compose -f docker-compose.yml up -d --build
```

检查：

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs -f web-api
curl -fsS http://127.0.0.1/healthz
```

升级、停止和查看 Worker 日志：

```bash
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml logs -f web-worker
docker compose -f docker-compose.yml down
```

Docker 配置特意不带 Podman 的 `:Z` SELinux 挂载标记；若改回 Podman，请使用
`podman-compose.yml` 和 [README.md](README.md)。
