FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend
COPY backend/requirements-bank.txt /app/backend/requirements-bank.txt
RUN python -m pip install --no-cache-dir -r requirements-bank.txt
COPY backend/lolarena /app/backend/lolarena
COPY backend/bank /app/backend/bank
COPY backend/config /app/backend/config
