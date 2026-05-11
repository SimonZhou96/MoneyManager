# Web Stock Screener Deployment

First deployment target: Alibaba Cloud Hong Kong ECS with Podman Compose.

## Prepare

```bash
sudo mkdir -p /data/moneymanager/mysql /data/moneymanager/redis /data/moneymanager/artifacts
cd /path/to/MoneyManager/stock_screener
cp .env.example .env
```

Set at least:

```env
MYSQL_ROOT_PASSWORD=change-me
MYSQL_PASSWORD=change-me
WEB_BOOTSTRAP_USERNAME=your-user
WEB_BOOTSTRAP_PASSWORD=your-password
WEB_COOKIE_SECURE=1
AGENT_TOKEN=long-random-token
DOMAIN_NAME=your-domain.com
```

## Start

```bash
cd deploy
podman compose -f podman-compose.yml up -d --build
```

## Create or Rotate Login User

```bash
podman compose -f deploy/podman-compose.yml exec web-api \
  python -m web.bootstrap_user --username your-user
```

## Local Agent

Run this on the machine where Futu OpenD is available:

```bash
cd /path/to/MoneyManager/stock_screener
python3 local_agent.py \
  --cloud-api-base https://your-domain.com \
  --agent-token "$AGENT_TOKEN" \
  --markets HK,US,A \
  --timeframes 1d,30m,5m \
  --max-codes-per-market 100
```

The cloud service never connects to Futu OpenD. It only receives HTTPS pushes from the local Agent.

For local execution with an env file:

```bash
cd stock_screener
cat > .agent.env <<'EOF'
CLOUD_API_BASE=https://your-domain.com
AGENT_TOKEN=long-random-token
AGENT_MARKETS=HK,US,A
AGENT_TIMEFRAMES=1d
AGENT_API_RETRIES=3
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
EOF
scripts/run_local_agent.sh --markets HK,US,A --timeframes 1d --dry-run
scripts/run_local_agent.sh --markets HK,US,A --timeframes 1d
```
