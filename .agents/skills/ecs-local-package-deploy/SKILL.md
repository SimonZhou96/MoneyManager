---
name: ecs-local-package-deploy
description: Build and deploy MoneyManager stock_screener from the local workspace to Alibaba Cloud ECS with packaged Linux images, without GitHub Actions. Use for requested local releases or ECS schema migrations.
---

# MoneyManager 本地打包部署到 ECS

## 适用范围与边界

- 用于把当前本地 `stock_screener` 代码和镜像打包后发布到既有 ECS；流程不依赖 GitHub Actions、GitHub Secrets 或 ECS 上的 `git pull`。
- 只有用户明确要求部署、远端数据库变更或服务维护时，才执行对应 ECS 写操作。数据库恢复、删除数据卷、重置凭据、修改安全组或重启实例需要单独明确授权。
- 不把 AccessKey 当作 SSH 密码，不在命令、日志、技能文件或打包文件中输出 AccessKey、私钥、`.env` 内容或模型/API 密钥。
- `workbench` 使用 AccessKey 调用阿里云控制面；远程 shell 仍依赖实例云助手客户端。文件传输使用已配置并校验过主机指纹的 SSH/SCP。不要关闭主机密钥校验。

## 发布流程

1. **检查项目和目标环境。** 先阅读仓库 `AGENTS.md`、[ECS 部署说明](../../../stock_screener/deploy/README.md) 和本次改动涉及的迁移文档。检查 `git status`、相关 diff，特别审阅 `deploy/Caddyfile`、Compose 文件和配置样例；只发布用户意图包含的改动。通过现有容器/Compose 标签或用户提供的信息确认 ECS、地域、SSH 用户/端口、部署目录、运行时（Docker 或 Podman）和当前 Compose 项目。不要猜一个路径后启动第二套服务。
2. **确认连通性。** 使用已配置的 `workbench`/SSH 配置，不索取或打印凭据。确认远端命令和文件传输都可用。若 Workbench 报 `CloudAssistantNotRunning` / `ClientNotRunning`，或 SSH 无 banner、connection reset，停止远端操作并报告具体错误；不要连续重试、重启实例、放宽安全组或改 IAM 来绕过问题。恢复云助手或 SSH 后再继续。
3. **在本机生成 Linux 镜像。** 根据 ECS 架构构建；本项目 ECS 为 `linux/amd64` 时使用本地 Docker Buildx：

   ```bash
   docker buildx build --platform linux/amd64 --load \
     -f stock_screener/deploy/Containerfile \
     -t moneymanager/web:latest stock_screener
   docker buildx build --platform linux/amd64 --load \
     -f stock_screener/deploy/Containerfile.frontend \
     -t moneymanager/frontend:latest stock_screener
   docker save moneymanager/web:latest moneymanager/frontend:latest | gzip -c > moneymanager-images.tar.gz
   ```

   `stock_screener/.dockerignore` 和 `.containerignore` 必须排除 `.env`、`.agent.env`、虚拟环境和运行时数据。构建前检查本地生成物也被排除；本地 `.playwright-cli`、artifacts 等若未被 ignore，先使用过滤后的临时构建上下文或补充 ignore，再构建镜像。另打包 `stock_screener/` 源文件供远端 Compose 配置和运维脚本使用；排除 `.env*`、`.venv`、`.runtime_home`、日志、缓存、`web_frontend/node_modules`、`web_frontend/dist`、`.deploy` 和本地 Playwright 产物。上传前检查归档清单，确认没有凭据、数据库转储或本地运行数据；记录两个归档的 SHA-256。归档、SCP、远端备份和 Compose 命令模板见 [本地发布操作手册](references/local-runbook.md)。
4. **上传并核验。** 使用 SCP/SSH 把源代码包和镜像归档传到远端临时目录，比较本地和远端 SHA-256。此时只暂存，不覆盖活动部署文件。不要覆盖服务器 `.env`、MySQL/Redis 数据目录、artifacts、证书数据或其他持久化目录。部署前为当前 `latest` 镜像保留带时间戳的回滚 tag（若镜像已存在）。
5. **先准备数据库变更。** 只有本次版本确实需要 schema/data migration 时执行本步骤。确认维护窗口，按项目任务锁/队列约定处理运行中的筛选任务，停止 `web-api` 和 `web-worker`，保持 MySQL/Redis 运行。使用远端持久化备份目录创建数据库转储，设置 `umask 077`，备份成功后检查文件非空并通过 `gzip -t`。先读具体 migration 脚本和文档，再执行指定变更；不要把全部初始化 SQL 当作通用迁移重复执行。
6. **同步源码并加载镜像。** 在备份成功后，把暂存源码同步到已确认的部署目录，保留服务器 `.env` 和运行时目录；使用现有运行时加载镜像（`docker load` 或 `podman load`）。按 ECS 上的 Compose 文件执行 `compose config -q`。若有 migration，在此时、服务仍停止的情况下运行；不要在应用启动代码中调用交互迁移脚本。

   账号 UUID 迁移时，按[账号配置迁移说明](../../../stock_screener/docs/account-provider-settings.md)操作：确认稳定的 `ACCOUNT_CONFIG_ENCRYPTION_KEY` 已安全配置且 API/worker 共用；加载新 web 镜像后，通过 Compose 的一次性 web 容器运行 `python scripts/migrate_account_user_ids_to_uuid.py`，向 stdin 传入 `MIGRATE-UUID`。
7. **启动并验收。** 使用对应 Compose 文件（Docker 用 `deploy/docker-compose.yml`；Podman 用 `deploy/podman-compose.yml`），以 `up -d --no-build --remove-orphans` 启动。检查 Compose 服务状态、web API `/healthz`、前端静态文件和 Caddy 配置校验；从本机访问实际域名的 health endpoint。涉及迁移时，查询目标列类型、账号 UUID、归属记录映射和孤儿记录，再确认登录与 API 配置页。汇报本地版本、镜像与代码包校验和、远端备份路径、迁移结果、健康检查和未完成项。不要把“命令已发出”描述为部署完成。

## 失败处理

- 上传、备份、迁移或健康检查任一步失败时，停止后续步骤并保留诊断信息、远端备份和旧镜像 tag；不要自动恢复数据库、删除数据或反复重启。
- 若只是代码/镜像回归，先判断旧镜像回滚是否与已执行 schema 兼容，再恢复旧 tag。数据库恢复会覆盖远端数据，必须在执行前另行确认。
- 远端连接不通时，只报告已验证的本地构建/打包状态和具体阻塞点；不要声称代码或数据库已更新到 ECS。
