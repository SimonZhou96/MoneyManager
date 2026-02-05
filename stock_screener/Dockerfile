FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1

CMD ["python", "daily_job.py", "--markets", "HK,US", "--db", "data/market_data.db", "--log", "logs/daily_sync.jsonl", "--loop"]
