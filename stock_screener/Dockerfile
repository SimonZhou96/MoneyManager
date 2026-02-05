FROM python:3.12-slim

# 设置元数据
LABEL maintainer="MoneyManager"
LABEL description="股票筛选器 - 每日数据入库任务"
LABEL version="1.0"

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

# 设置工作目录
WORKDIR /app

# 创建非 root 用户（安全性最佳实践）
RUN groupadd -r appuser && useradd -r -g appuser appuser

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt /app/

# 升级 pip 并安装依赖
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

# 创建必要的目录
RUN mkdir -p /app/logs /app/data /app/cache && \
    chown -R appuser:appuser /app

# 再复制应用代码
COPY --chown=appuser:appuser . /app

# 切换到非 root 用户
USER appuser

# 健康检查（可选，用于监控容器状态）
HEALTHCHECK --interval=30m --timeout=10s --start-period=5m --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# 默认命令
CMD ["python", "daily_job.py", "--markets", "HK,US", "--log", "logs/daily_sync.jsonl", "--loop"]
