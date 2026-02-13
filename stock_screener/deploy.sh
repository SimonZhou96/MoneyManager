#!/bin/bash

# Podman 部署脚本 for daily_job.py
# 使用方法: ./deploy.sh [build|run|stop|logs]

set -e

IMAGE_NAME="stock-screener"
CONTAINER_NAME="stock-screener"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 默认配置（可通过环境变量覆盖）
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
    
    # 创建日志目录
    mkdir -p "$SCRIPT_DIR/logs"
    
    # 检查容器是否已存在
    if podman ps -a --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        echo "⚠️  容器已存在，先删除..."
        podman rm -f "$CONTAINER_NAME" || true
    fi
    
    # 运行容器
    if [ "$LOOP" = "true" ]; then
        echo "📦 以循环模式运行（每 ${INTERVAL_HOURS} 小时执行一次）..."
        podman run -d --name "$CONTAINER_NAME" \
            -v "$SCRIPT_DIR/logs:/app/logs" \
            -e MYSQL_HOST="$MYSQL_HOST" \
            -e MYSQL_PORT="$MYSQL_PORT" \
            -e MYSQL_USER="$MYSQL_USER" \
            -e MYSQL_PASSWORD="$MYSQL_PASSWORD" \
            -e MYSQL_DATABASE="$MYSQL_DATABASE" \
            "$IMAGE_NAME:latest" \
            python daily_job.py \
                --markets "$MARKETS" \
                --timeframe "$TIMEFRAME" \
                --mysql-host "$MYSQL_HOST" \
                --mysql-port "$MYSQL_PORT" \
                --mysql-user "$MYSQL_USER" \
                --mysql-password "$MYSQL_PASSWORD" \
                --mysql-database "$MYSQL_DATABASE" \
                --log logs/daily_sync.jsonl \
                --loop \
                --interval-hours "$INTERVAL_HOURS"
    else
        echo "📦 单次执行模式..."
        podman run --rm --name "${CONTAINER_NAME}-once" \
            -v "$SCRIPT_DIR/logs:/app/logs" \
            -e MYSQL_HOST="$MYSQL_HOST" \
            -e MYSQL_PORT="$MYSQL_PORT" \
            -e MYSQL_USER="$MYSQL_USER" \
            -e MYSQL_PASSWORD="$MYSQL_PASSWORD" \
            -e MYSQL_DATABASE="$MYSQL_DATABASE" \
            "$IMAGE_NAME:latest" \
            python daily_job.py \
                --markets "$MARKETS" \
                --timeframe "$TIMEFRAME" \
                --mysql-host "$MYSQL_HOST" \
                --mysql-port "$MYSQL_PORT" \
                --mysql-user "$MYSQL_USER" \
                --mysql-password "$MYSQL_PASSWORD" \
                --mysql-database "$MYSQL_DATABASE" \
                --log logs/daily_sync.jsonl
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
    podman logs -f "$CONTAINER_NAME" 2>/dev/null || echo "容器未运行，查看文件日志: tail -f $SCRIPT_DIR/logs/daily_sync.jsonl"
}

status() {
    echo "📊 容器状态:"
    podman ps -a --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || echo "容器不存在"
}

case "${1:-}" in
    build)
        build
        ;;
    run)
        run
        ;;
    stop)
        stop
        ;;
    logs)
        logs
        ;;
    status)
        status
        ;;
    *)
        echo "使用方法: $0 {build|run|stop|logs|status}"
        echo ""
        echo "命令说明:"
        echo "  build   - 构建镜像"
        echo "  run     - 运行容器（循环模式，默认）"
        echo "  stop    - 停止并删除容器"
        echo "  logs    - 查看容器日志"
        echo "  status  - 查看容器状态"
        echo ""
        echo "环境变量配置:"
        echo "  MYSQL_HOST       - MySQL 主机地址（默认: 127.0.0.1）"
        echo "  MYSQL_PORT       - MySQL 端口（默认: 3306）"
        echo "  MYSQL_USER       - MySQL 用户名（默认: root）"
        echo "  MYSQL_PASSWORD  - MySQL 密码（必需）"
        echo "  MYSQL_DATABASE  - MySQL 数据库名（默认: market_data）"
        echo "  MARKETS          - 市场列表（默认: HK,US,A）"
        echo "  TIMEFRAME        - K线周期（默认: 1d）"
        echo "  LOOP             - 是否循环执行（默认: true）"
        echo "  INTERVAL_HOURS   - 循环间隔小时（默认: 24）"
        echo ""
        echo "示例:"
        echo "  MYSQL_PASSWORD=your_password $0 build"
        echo "  MYSQL_PASSWORD=your_password $0 run"
        echo "  MYSQL_PASSWORD=your_password MARKETS=HK LOOP=false $0 run"
        exit 1
        ;;
esac
