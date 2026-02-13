#!/bin/bash
# 启动选股器 API 服务

cd "$(dirname "$0")"

# 检查是否在虚拟环境中
if [ -z "$VIRTUAL_ENV" ]; then
    echo "警告：未检测到虚拟环境，建议先激活虚拟环境"
    echo "例如：source .venv/bin/activate"
    echo ""
fi

# 检查依赖
echo "检查依赖..."
python3 -c "import fastapi" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "正在安装依赖..."
    pip install -r requirements.txt
fi

# 设置环境变量（如果需要）
export MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
export MYSQL_PORT="${MYSQL_PORT:-3306}"
export MYSQL_USER="${MYSQL_USER:-root}"
export MYSQL_PASSWORD="${MYSQL_PASSWORD:-123456}"
export MYSQL_DATABASE="${MYSQL_DATABASE:-market_data}"

# 启动服务
echo "启动 API 服务..."
echo "访问地址: http://localhost:8000"
echo "API 文档: http://localhost:8000/docs"
echo ""

python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
