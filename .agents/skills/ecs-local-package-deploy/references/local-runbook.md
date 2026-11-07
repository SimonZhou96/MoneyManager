# 本地打包发布命令模板

从仓库根目录执行。命令中的 ECS 地址、SSH 端口、登录别名和实际部署目录都应从现有 SSH 配置、实例和运行中的 Compose 服务确认；不要照抄示例路径启动第二套服务。

## 本地构建并打包

确认本机 Docker Buildx 可用，目标 ECS 架构为 `linux/amd64`。镜像构建上下文是 `stock_screener/`，`.dockerignore` 排除了根目录 `.env`、`.agent.env`、虚拟环境、日志及常见前端构建产物。构建前还要检查 `stock_screener/` 内是否有本地生成的 `.playwright-cli`、artifacts 或其他敏感/运行时文件；当前 ignore 规则未覆盖所有这类路径。若存在，先将这些路径加入忽略规则或用过滤后的临时构建上下文，不能把它们写入镜像层。

```bash
set -euo pipefail
MM_DEPLOY_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$MM_DEPLOY_TMP_DIR"' EXIT

docker buildx build --platform linux/amd64 --load \
  -f stock_screener/deploy/Containerfile \
  -t moneymanager/web:latest stock_screener
docker buildx build --platform linux/amd64 --load \
  -f stock_screener/deploy/Containerfile.frontend \
  -t moneymanager/frontend:latest stock_screener

docker save moneymanager/web:latest moneymanager/frontend:latest \
  | gzip -c > "$MM_DEPLOY_TMP_DIR/moneymanager-images.tar.gz"

tar -C stock_screener \
  --exclude='./.env*' --exclude='*/.env*' --exclude='*.env*' \
  --exclude='./.agent.env' --exclude='./*/.agent.env' \
  --exclude='./.venv' --exclude='./*/.venv' \
  --exclude='./.runtime_home' --exclude='./*/.runtime_home' \
  --exclude='./logs' --exclude='./*/logs' \
  --exclude='./__pycache__' --exclude='./*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='./web_frontend/node_modules' \
  --exclude='./web_frontend/dist' \
  --exclude='./web_frontend/.npm-cache' \
  --exclude='*/.npm-cache' --exclude='*/.vite' --exclude='*/.cache' \
  --exclude='*/.pytest_cache' --exclude='*/.mypy_cache' --exclude='*/.ruff_cache' \
  --exclude='./.deploy' --exclude='./*/.deploy' \
  --exclude='./.playwright-cli' --exclude='*/.playwright-cli' \
  --exclude='./artifacts' --exclude='./*/artifacts' \
  --exclude='*.sql.gz' --exclude='*.sqlite*' --exclude='*.dump' \
  --exclude='.DS_Store' --exclude='*/.DS_Store' \
  -czf "$MM_DEPLOY_TMP_DIR/stock_screener-source.tar.gz" .

shasum -a 256 \
  "$MM_DEPLOY_TMP_DIR/moneymanager-images.tar.gz" \
  "$MM_DEPLOY_TMP_DIR/stock_screener-source.tar.gz"
tar -tzf "$MM_DEPLOY_TMP_DIR/stock_screener-source.tar.gz" \
  > "$MM_DEPLOY_TMP_DIR/source-manifest.txt"
python3 - "$MM_DEPLOY_TMP_DIR/stock_screener-source.tar.gz" <<'PY'
import sys
import tarfile

blocked_parts = {".venv", ".runtime_home", "__pycache__", "node_modules", "dist", "logs", ".deploy", ".playwright-cli", "artifacts", ".DS_Store", ".npm-cache", ".vite", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
with tarfile.open(sys.argv[1], "r:gz") as archive:
    unsafe = []
    for member in archive.getmembers():
        name = member.name[2:] if member.name.startswith("./") else member.name
        parts = name.split("/")
        if (
            any(part.startswith(".env") or part == ".agent.env" or part in blocked_parts for part in parts)
            or name.endswith((".sql.gz", ".sqlite", ".sqlite3", ".dump"))
        ):
            unsafe.append(member.name)
    if unsafe:
        raise SystemExit("Refusing package containing excluded entries: " + ", ".join(unsafe[:20]))
PY
```

Review the manifest and both `git status` and diffs before upload. The tar exclude patterns are defensive; if the manifest contains environment files, local databases, secrets, `.venv`, logs, or artifacts, discard the package and fix the exclusions first. Reconcile local edits to `deploy/Caddyfile` and Compose files with the intended server configuration.

## Transfer and remote deploy

Create a mode-700 incoming directory on the confirmed ECS host, transfer both archives with SCP, and compare `shasum -a 256` locally with `sha256sum` remotely. Use the host key already verified for the instance; do not use `StrictHostKeyChecking=no` or add an unverified key automatically.

Use the user's configured SSH alias or key agent; keep concrete address, port, and paths in shell variables, not in the skill. For example, create the incoming directory with `ssh`, then upload both archives using `scp -P "$MM_ECS_SSH_PORT"`. On ECS, extract source into the incoming directory and inspect its manifest before syncing.

On ECS, from the confirmed deployment directory:

1. Confirm the incoming archive checksums, free disk space, Compose project path, runtime, and that the server `.env` exists. Keep `.env` mode `600`; never upload the local `.env`.
2. If the previous images exist, tag `moneymanager/web:latest` and `moneymanager/frontend:latest` with a timestamped rollback tag before loading new images.
3. If a schema migration is required, follow the backup procedure below before replacing active files or images. Stop only the API/worker after checking active screening work; keep MySQL/Redis up.

## Verified database backup before a migration

From the confirmed deployment directory, define `compose` to match the installed runtime, then check `compose config -q`. For example:

```bash
compose() { docker compose -f deploy/docker-compose.yml "$@"; }
# Or for Podman:
# compose() { podman compose -f deploy/podman-compose.yml "$@"; }
```

Keep MySQL running while stopping only the API and worker. Check the project's task-lock/queue guidance before interrupting active screening work.

```bash
set -euo pipefail
umask 077
BACKUP_DIR=/data/moneymanager/backups/mysql
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/pre-deploy-$STAMP.sql.gz"
DUMP_FILE="$(mktemp "$BACKUP_DIR/.dump.XXXXXX")"
trap 'rm -f "$DUMP_FILE"' EXIT

compose stop web-api web-worker
compose exec -T mysql sh -ec \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --single-transaction --routines --triggers -uroot "$MYSQL_DATABASE"' \
  > "$DUMP_FILE"
test -s "$DUMP_FILE"
gzip -c "$DUMP_FILE" > "$BACKUP_FILE"
gzip -t "$BACKUP_FILE"
test -s "$BACKUP_FILE"
```

Keep the backup path and verification result in the deployment notes. Do not print the MySQL password or add it to command arguments. If backup or integrity verification fails, do not run the migration.

## Sync, migrate, and start

Once the backup is verified (when needed), sync staged files into the confirmed deployment directory and load the new image archive. If `rsync` is available, use `rsync -a --delete` with excludes for `*.env*`, `.venv/`, `.runtime_home/`, `logs/`, `.deploy/`, `artifacts/`, and any confirmed runtime data directories; this removes stale code files while protecting server-owned state. Otherwise use a reviewed tar overlay and remove only known obsolete code files. Never run broad `rm -rf` against the deployment or data paths. Validate the new Compose config before starting.

```bash
docker compose -f deploy/docker-compose.yml config -q
```

For Podman use `podman compose -f deploy/podman-compose.yml ...` or the installed `podman-compose -f deploy/podman-compose.yml ...`. Verify the chosen implementation's option syntax. If a migration is required, run it now while `web-api` and `web-worker` remain stopped; start the application only after it succeeds:

```bash
docker compose -f deploy/docker-compose.yml up -d --no-build --remove-orphans
```

Do not use `down -v`, remove named volumes, overwrite `.env`, or recreate MySQL/Redis data directories.

## Account UUID migration example

Use this only when deploying the account UUID schema change. Read `stock_screener/docs/account-provider-settings.md` first. Confirm `ACCOUNT_CONFIG_ENCRYPTION_KEY` is already set to the stable production value for API and worker processes; never print it or silently replace it.

After the new web image is loaded and `web-api`/`web-worker` are stopped, run the migration through the Compose network so it can reach MySQL:

```bash
printf 'MIGRATE-UUID\n' \
  | compose run --rm --no-deps -T web-api \
      python scripts/migrate_account_user_ids_to_uuid.py
```

If the active Compose implementation does not support these flags, inspect its `run --help` and preserve the non-interactive confirmation input. Do not improvise SQL that drops/recreates ownership columns. After the migration succeeds, start services with the Compose `up` command above and verify UUID column types, mapped account ownership, login, and the API 配置 page.

## Post-deploy checks

Check `compose ps`, then run the service-specific checks:

```bash
compose exec -T web-api curl -fsS http://127.0.0.1:8000/healthz
compose exec -T frontend sh -c 'test -s /usr/share/nginx/html/index.html'
compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile
```

From the local machine, check the configured public endpoint's `/healthz`. If any health check fails, capture `compose logs --tail=200 web-api web-worker frontend caddy`, stop the rollout, and report status. Do not restore a database backup automatically; restoration overwrites production data and requires separate confirmation.
