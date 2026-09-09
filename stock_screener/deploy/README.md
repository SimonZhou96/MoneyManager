# MoneyManager Web 选股器云端部署教程

本文档用于把 Web 选股器部署到中国大陆 ECS，并通过本地 OpenD Agent 把富途 OpenD 数据推送到云端。本教程按你的域名 `mmmcashlife.top` 编写。

- 云服务器：Alibaba Cloud 中国大陆 ECS。
- 部署方式：Podman Compose。
- 云端服务：Caddy + frontend + web-api + web-worker + MySQL + Redis。
- 数据流：服务默认使用 YFinance/AKShare；仅在配置健康的远端 OpenD 后优先使用 OpenD 行情。

> Redis 在当前 compose 中用于 RQ 后台任务队列，不用于 Web API 限流；Web API 限流是进程内滑动窗口。
> 中国大陆 ECS 对外提供网站访问通常需要 ICP 备案。备案完成前，不要把域名正式解析到大陆 ECS 并开放 Web 访问。
> OpenAI/Codex API 不建议从中国大陆 ECS 直连；大陆 ECS 上建议优先关闭 Codex/OpenAI provider，或把 AI worker 放到 OpenAI 支持地区。

## 1. 备案与域名准备

中国大陆 ECS 建站的关键前置条件是 ICP 备案：

1. 确认 `mmmcashlife.top` 已完成实名认证。
2. 在阿里云 ICP 备案系统提交备案：
   - 接入商：阿里云。
   - 服务类型：网站。
   - 服务器：选择你的中国大陆 ECS。
   - 域名：`mmmcashlife.top`。
3. 备案通过后，按要求在网站页脚展示备案号。
4. 备案通过后再配置 DNS：
   - 主机记录：`@`
   - 记录类型：`A`
   - 记录值：ECS 公网 IP
   - 可选：`www` CNAME 到 `mmmcashlife.top`

备案未完成时，可以先用 ECS 公网 IP 做服务器初始化和容器部署，但不要作为正式公网网站使用。

参考：

- 阿里云 ICP 备案：https://beian.aliyun.com/
- 阿里云 ICP 备案文档：https://help.aliyun.com/zh/icp-filing/
- 工信部备案系统：https://beian.miit.gov.cn/

## 2. 创建 ECS 与网络

1. 在 Alibaba Cloud ECS 创建实例：
   - 地域：中国大陆，例如华东、华南、华北，优先选择你常用访问地附近的地域。
   - 规格：小规模试运行用 `2 vCPU / 4 GB RAM / 80-100 GB ESSD`。
   - 系统：推荐 Ubuntu 22.04/24.04 LTS，命令更统一。
   - 公网：绑定公网 IP。
2. 安全组入站规则：
   - TCP `80`：允许 `0.0.0.0/0`。
   - TCP `443`：允许 `0.0.0.0/0`。
   - TCP `22`：只允许你的固定公网 IP。
   - 不开放 MySQL `3306`、Redis `6379`。
3. 域名 DNS：
   - 添加 `A` 记录，例如 `mmmcashlife.top -> ECS 公网 IP`。
   - 中国大陆 ECS 请在 ICP 备案通过后再正式解析。
   - 等 DNS 生效后再启动 Caddy，Caddy 会自动申请 HTTPS 证书。

参考：

- Alibaba Cloud ECS 创建实例：https://www.alibabacloud.com/help/en/ecs/user-guide/create-instances/
- Alibaba Cloud 安全组：https://www.alibabacloud.com/help/en/ecs/user-guide/manage-ecs-instances-in-security-groups
- Caddy 自动 HTTPS：https://caddyserver.com/docs/automatic-https

## 3. 初始化服务器

SSH 登录 ECS：

```bash
ssh root@YOUR_ECS_PUBLIC_IP
```

安装基础依赖。Ubuntu 推荐：

```bash
apt-get update
apt-get install -y git curl vim python3 python3-pip podman podman-compose
podman --version
podman compose version || podman-compose --version
```

如果系统没有 `podman compose`，后续命令可改用 `podman-compose`。本文以 `podman compose` 为准。

创建持久化目录：

```bash
mkdir -p /data/moneymanager/mysql
mkdir -p /data/moneymanager/redis
mkdir -p /data/moneymanager/artifacts
mkdir -p /data/moneymanager/backups/mysql
```

## 4. 上传代码

在服务器上拉取代码：

```bash
mkdir -p /opt
cd /opt
git clone <YOUR_REPO_URL> MoneyManager
cd /opt/MoneyManager/stock_screener
```

如果代码仓库是私有仓库，可以先在 ECS 上配置 SSH key，或从本地打包上传：

```bash
# 本地执行
tar --exclude='__pycache__' --exclude='.venv' --exclude='logs' -czf moneymanager.tar.gz MoneyManager
scp moneymanager.tar.gz root@YOUR_ECS_PUBLIC_IP:/opt/

# 服务器执行
cd /opt
tar -xzf moneymanager.tar.gz
cd /opt/MoneyManager/stock_screener
```

## 5. 配置环境变量

复制样例：

```bash
cd /opt/MoneyManager/stock_screener
cp .env.example .env
chmod 600 .env
vim .env
```

至少配置以下字段：

```env
MYSQL_HOST=mysql
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<和 MYSQL_ROOT_PASSWORD 保持一致>
MYSQL_DATABASE=market_data
MYSQL_ROOT_PASSWORD=<强密码>

WEB_SESSION_COOKIE=moneymanager_session
WEB_SESSION_TTL_HOURS=24
WEB_COOKIE_SECURE=1
WEB_BOOTSTRAP_USERNAME=<你的登录账号>
WEB_BOOTSTRAP_PASSWORD=<你的登录密码>
ARTIFACT_DIR=/data/moneymanager/artifacts

REDIS_URL=redis://redis:6379/0
AGENT_TOKEN=<长随机 token，建议 32 字节以上>
DOMAIN_NAME=mmmcashlife.top

KLINE_USE_FUTU_OPEND=0
FUTU_HOST=<内网 OpenD 地址，例如 10.0.0.8>
FUTU_PORT=11111
```

如果要启用飞书发送，再补充：

```env
FEISHU_WEBHOOK_URL=
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_CHAT_ID=
```

中国大陆 ECS 不建议直连 OpenAI/Codex。大陆 ECS 上推荐先关闭 OpenAI/Codex provider，只保留国内可访问 provider，或者先关闭 AI 分析：

```env
ENABLE_LLM_ANALYSIS=0
```

如果你确认要在大陆 ECS 上启用可访问的模型 provider，例如 DeepSeek，可配置：

```env
ENABLE_LLM_ANALYSIS=1
TAVILY_API_KEY=
ZHIPUAI_API_KEY=
SIGNAL_SEARCH_PROVIDER_ORDER=tavily,zhipuai
LLM_PROVIDER_ORDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_LLM_MODEL=
DEEPSEEK_API_BASE=
```

如果后续要使用 Codex/OpenAI，建议单独部署香港/新加坡/日本等 OpenAI 支持地区的 AI worker，而不是让大陆 ECS 直接请求 OpenAI API。

### Market Intel

`market_intel` 提供后端缓存的市场和个股证据，用于 Web 页面查看，也可以选择性增强 `signal_analysis`。

- API 路由位于 `/api/market-intel`。
- 表结构由 `MarketDatabase.init_market_intel_schema()` 初始化。
- Provider 失败会记录到 `market_intel_provider_runs`。
- Market Intel provider 失败不会阻断筛选任务。
- `SIGNAL_ENABLE_MARKET_INTEL=0` 时，自动 AI 分析保持原有搜索 + LLM 路径。
- 只有在目标环境确认 provider 缓存行为正常后，再设置 `SIGNAL_ENABLE_MARKET_INTEL=1`。

### Market Intel source expansion

Stable HTTP providers are controlled by `MARKET_INTEL_PROVIDER_ORDER` and the
per-provider `MARKET_INTEL_ENABLE_*` flags. The default order is:

`cailianpress,sina,tradingview,eastmoney,global_index`

`SIGNAL_ENABLE_MARKET_INTEL=0` keeps richer provider data out of automated
signal analysis by default. Turning on live Market Intel for the API/UI does not
increase screening search or LLM calls unless `SIGNAL_ENABLE_MARKET_INTEL=1` is
also set.

Search-like providers (`iwencai`, `eastmoney_search`) and browser/cookie
providers (`xueqiu`) stay disabled until their contracts and operating limits
are verified. `MARKET_INTEL_BROWSER_PROVIDERS_ALLOWED=0` prevents browser-backed
providers from running in normal screening paths.

生成 `AGENT_TOKEN` 示例：

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

## 6. 启动云端服务

```bash
cd /opt/MoneyManager/stock_screener/deploy
podman compose -f podman-compose.yml up -d --build
```

查看容器：

```bash
podman ps
```

查看日志：

```bash
podman compose -f podman-compose.yml logs -f web-api
podman compose -f podman-compose.yml logs -f caddy
podman compose -f podman-compose.yml logs -f web-worker
```

健康检查：

```bash
curl -s https://mmmcashlife.top/healthz
```

预期返回：

```json
{"ok":true}
```

浏览器访问：

```text
https://mmmcashlife.top
```

使用 `.env` 中的 `WEB_BOOTSTRAP_USERNAME / WEB_BOOTSTRAP_PASSWORD` 登录。

如果备案尚未完成、域名未解析，可以先用 ECS 内部或 SSH 端口转发做部署检查：

```bash
curl -s http://127.0.0.1/healthz
```

或者从本地临时转发：

```bash
ssh -L 8080:127.0.0.1:80 root@YOUR_ECS_PUBLIC_IP
```

然后访问：

```text
http://127.0.0.1:8080
```

## 7. 初始化或轮换登录账号

首次启动时，`web-api` 会根据 `.env` 自动创建 bootstrap 用户。如果要修改密码：

```bash
cd /opt/MoneyManager/stock_screener
podman compose -f deploy/podman-compose.yml exec web-api \
  python -m web.bootstrap_user --username <你的登录账号> --password '<新密码>'
```

## 8. 初始化规则与数据库结构

`web-api` 启动时会自动初始化 Web、股票池、板块、K 线缓存、规则链和 AI 分析相关表。为了确保已有数据库规则参数也同步到当前版本，可以手动执行 SQL：

```bash
cd /opt/MoneyManager/stock_screener
set -a
source .env
set +a
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/001_screening_rules.sql
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/002_signal_analysis.sql
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/003_sector_memberships.sql
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/004_update_zuoyi_signal_window_15.sql
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/005_web_platform.sql
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysql -uroot -p"$MYSQL_ROOT_PASSWORD" market_data < sql/006_enable_hard_filters.sql
```

如果没有在当前 shell 加载 `.env`，`MYSQL_ROOT_PASSWORD` 会是空值。重新执行：

```bash
cd /opt/MoneyManager/stock_screener
set -a
source .env
set +a
echo "$MYSQL_ROOT_PASSWORD" | wc -c
```

## 9. 启动筛选任务

登录网页后：

1. 打开“全市场筛选”。
2. 选择 `HK / US / A`。
3. 选择周期，例如 `1d`。
4. 按需启用 AI 分析和飞书发送。
5. 点击“启动筛选”。
6. 到“总览”或“任务详情”查看进度和导出文件。

注意：

- Web API 需要登录态。
- 每天每个 `market + timeframe` 只允许成功跑一次；运行中的重复请求会复用同一个任务，已成功的重复请求会返回中文提示。
- Web API 业务错误统一返回 HTTP 200 + `ok=false + message`，前端会弹出中文错误。
- 搜索/LLM/飞书属于 best-effort，失败不应影响原始 CSV 生成。

## 10. 日常维护

升级代码：

```bash
cd /opt/MoneyManager
git pull
cd stock_screener/deploy
podman compose -f podman-compose.yml up -d --build
```

备份 MySQL：

```bash
BACKUP=/data/moneymanager/backups/mysql/$(date +%F).sql.gz
cd /opt/MoneyManager/stock_screener
set -a
source .env
set +a
podman compose -f deploy/podman-compose.yml exec -T mysql \
  mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" market_data | gzip > "$BACKUP"
ls -lh "$BACKUP"
```

清理 14 天前备份：

```bash
find /data/moneymanager/backups/mysql -name '*.sql.gz' -mtime +14 -delete
```

查看磁盘：

```bash
df -h
du -sh /data/moneymanager/*
```

停止服务：

```bash
cd /opt/MoneyManager/stock_screener/deploy
podman compose -f podman-compose.yml down
```

## 11. 常见问题

### 域名打不开

检查：

```bash
dig +short mmmcashlife.top
curl -I http://mmmcashlife.top
curl -I https://mmmcashlife.top
podman compose -f deploy/podman-compose.yml logs caddy
```

确认 DNS 指向 ECS 公网 IP，安全组开放 `80/443`。

中国大陆 ECS 还需要确认 ICP 备案已通过；备案未完成时，即使服务部署成功，公网域名访问也可能被拦截或无法正常开通。

### HTTPS 证书没有签发

Caddy 自动 HTTPS 需要：

- `DOMAIN_NAME` 是真实域名，不是 `localhost`。
- DNS 已解析到当前 ECS。
- 80/443 可从公网访问。
- `/data` 挂载可写，Caddy 证书存储能持久化。

中国大陆 ECS 还需要确保备案通过后再开放域名访问。

### Codex/OpenAI 调用失败

中国大陆 ECS 不建议直连 OpenAI/Codex API。推荐：

- 大陆 ECS 上设置 `ENABLE_LLM_ANALYSIS=0`，先跑基础筛选。
- 或设置 `LLM_PROVIDER_ORDER=deepseek`，并通过 `ZHIPUAI_API_KEY` 给搜索链增加国内可访问兜底。
- 或把 AI worker 部署到香港/新加坡/日本等 OpenAI 支持地区，让大陆主站只负责页面、任务和结果展示。

### 登录失败

检查 `.env` 中的 bootstrap 用户，或重置：

```bash
podman compose -f deploy/podman-compose.yml exec web-api \
  python -m web.bootstrap_user --username <你的登录账号>
```
