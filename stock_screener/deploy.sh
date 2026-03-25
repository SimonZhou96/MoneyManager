#!/bin/bash

# Podman 部署脚本 — 运行 scheduled_daily_job（捞池+筛选+CSV+飞书）
# 使用方法: ./deploy.sh [build|run|stop|logs|status]

set -e

IMAGE_NAME="stock-screener"
CONTAINER_NAME="stock-screener"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-123456}"
MYSQL_DATABASE="${MYSQL_DATABASE:-market_data}"
MARKETS="${MARKETS:-HK,US,A}"
TIMEFRAME="${TIMEFRAME:-1d}"
LOOP="${LOOP:-true}"
INTERVAL_HOURS="${INTERVAL_HOURS:-24}"

build() {
    echo "🔨 构建镜像..."
    cd "$SCRIPT_DIR"
    podman build -t "$IMAGE_NAME:latest" .
    echo "✅ 构建完成"
}

run() {
    echo "🚀 启动容器..."

    mkdir -p "$SCRIPT_DIR/logs"

    if podman ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        echo "⚠️  容器已存在，先删除..."
        podman rm -f "$CONTAINER_NAME" || true
    fi

    ENV_ARGS=(
        -e MYSQL_HOST="$MYSQL_HOST"
        -e MYSQL_PORT="$MYSQL_PORT"
        -e MYSQL_USER="$MYSQL_USER"
        -e MYSQL_PASSWORD="$MYSQL_PASSWORD"
        -e MYSQL_DATABASE="$MYSQL_DATABASE"
        -e FEISHU_WEBHOOK_URL="${FEISHU_WEBHOOK_URL:-}"
    )

    if [ "$LOOP" = "true" ]; then
        echo "📦 循环模式：每 ${INTERVAL_HOURS} 小时执行一次 scheduled_daily_job.py"
        podman run -d --name "$CONTAINER_NAME" \
            -v "$SCRIPT_DIR/logs:/app/logs" \
            "${ENV_ARGS[@]}" \
            -e MARKETS="$MARKETS" \
            -e TIMEFRAME="$TIMEFRAME" \
            -e INTERVAL_HOURS="$INTERVAL_HOURS" \
            "$IMAGE_NAME:latest" \
            bash -c 'while true; do python scheduled_daily_job.py --markets "$MARKETS" --timeframe "$TIMEFRAME"; sleep $((INTERVAL_HOURS * 3600)); done'
    else
        echo "📦 单次执行 scheduled_daily_job.py"
        podman run --rm --name "${CONTAINER_NAME}-once" \
            -v "$SCRIPT_DIR/logs:/app/logs" \
            "${ENV_ARGS[@]}" \
            "$IMAGE_NAME:latest" \
            python scheduled_daily_job.py --markets "$MARKETS" --timeframe "$TIMEFRAME"
    fi

    echo "✅ 容器已启动"
    echo "📋 查看日志: podman logs -f $CONTAINER_NAME"
}

stop() {
    echo "🛑 停止容器..."
    podman stop "$CONTAINER_NAME" 2>/dev/null || echo "容器未运行"
    podman rm "$CONTAINER_NAME" 2>/dev/null || echo "容器不存在"
    echo "✅ 已停止"
}

logs() {
    echo "📋 查看容器日志..."
    podman logs -f "$CONTAINER_NAME" 2>/dev/null || echo "容器未运行；可查看 $SCRIPT_DIR/logs/"
}

status() {
    echo "📊 容器状态:"
    podman ps -a --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || echo "容器不存在"
}

case "${1:-}" in
    build) build ;;
    run) run ;;
    stop) stop ;;
    logs) logs ;;
    status) status ;;
    *)
        echo "使用方法: $0 {build|run|stop|logs|status}"
        echo ""
        echo "环境变量: MYSQL_*, MARKETS, TIMEFRAME, LOOP, INTERVAL_HOURS, FEISHU_WEBHOOK_URL"
        exit 1
        ;;
esac
