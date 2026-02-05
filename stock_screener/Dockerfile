FROM python:3.12-slim

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1

CMD ["python", "daily_job.py", "--markets", "HK,US", "--log", "logs/daily_sync.jsonl", "--loop"]
