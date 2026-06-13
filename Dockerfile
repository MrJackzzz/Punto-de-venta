FROM python:3.13-slim
WORKDIR /app
RUN apt update && apt install -y libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev libpq-dev postgresql-client && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD gunicorn -w 4 -b 0.0.0.0:8000 app:app
