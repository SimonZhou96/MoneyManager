# 本机 Redis 与报告 ES

本机 Python 进程不能使用 Compose 内部服务名 `redis`。本机 `.env` 使用
`REDIS_URL=redis://127.0.0.1:6379/0`，容器部署继续使用 `redis://redis:6379/0`。

当前本机 Redis 配置在 `.runtime_home/redis/redis.conf`，仅监听回环地址，
开启 AOF。数据、ES 管理密码、CA 和应用 API Key 均保存在忽略目录
`.runtime_home/`，不得提交。Redis 仅供本机开发使用；不要绑定公网地址。

ES 使用独立 Podman 容器 `stock-screener-es`，镜像 9.5.5，内存上限 2 GiB，
关闭 ML，使用自动堆配置。宿主机仅发布 `127.0.0.1:9201`，启用 HTTPS。
数据与配置分别持久化在 `stock-screener-es-data`、`stock-screener-es-config` 卷。
原有其他项目的 ES 容器及数据不变。单节点不是高可用部署。

首次复制容器生成的 CA 后，用管理凭据初始化（应用不使用管理凭据）：

```sh
PYTHONPATH=. .venv/bin/python deploy/init_report_store.py \
  --url https://localhost:9201 \
  --ca .runtime_home/report-services/http_ca.crt \
  --admin-password-file .runtime_home/report-services/es-admin-password \
  --credentials-file .runtime_home/report-services/app.env
```

脚本创建单分片、零副本的报告索引，生成仅有报告索引读写权限的 API Key，
将应用配置写入权限为 0600 的文件，不输出凭据。文件已存在时拒绝覆盖。

从 `stock_screener` 目录启动 API 和 worker，分别在两个终端执行：

```sh
source .runtime_home/report-services/app.env
WEB_COOKIE_SECURE=0 .venv/bin/python -m uvicorn web.main:app --host 127.0.0.1 --port 8001
```

```sh
source .runtime_home/report-services/app.env
.venv/bin/python -m web.worker
```

前端使用 `VITE_BACKEND_URL=http://127.0.0.1:8001`，监听 5174。
`WEB_COOKIE_SECURE=0` 仅用于本机 HTTP，不修改生产配置。
重启依赖时使用 `podman start stock-screener-es` 和
`redis-server .runtime_home/redis/redis.conf`；不要删除卷或清空 Redis。
启动 worker 前先核对 Redis 队列，避免意外执行历史任务。
