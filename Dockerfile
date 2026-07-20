FROM python:3.13-slim
WORKDIR /app
RUN apt update && apt install -y gcc libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev libpq-dev postgresql-client && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD gunicorn -w 2 --threads 4 --timeout 120 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 --access-logfile '-' --error-logfile '-' --log-level warning -b 0.0.0.0:8000 app:app
