FROM python:3.11-slim AS full

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir \
    -r /app/requirements.txt

RUN pip install --no-cache-dir \
    "jieba>=0.42,<1"

COPY . /app

RUN mkdir -p \
    /app/data \
    /app/uploads \
    /app/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM python:3.11-slim AS api

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-api.txt /app/requirements-api.txt

RUN pip install --no-cache-dir \
    -r /app/requirements-api.txt

RUN pip install --no-cache-dir \
    "jieba>=0.42,<1"

COPY . /app

RUN mkdir -p \
    /app/data \
    /app/uploads \
    /app/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM python:3.11-slim AS agent

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-agent.txt /app/requirements-agent.txt

RUN pip install --no-cache-dir \
    -r /app/requirements-agent.txt

COPY . /app

RUN mkdir -p \
    /app/data \
    /app/uploads \
    /app/logs


FROM python:3.11-slim AS lite

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-lite.txt /app/requirements-lite.txt

RUN pip install --no-cache-dir \
    -r /app/requirements-lite.txt

COPY . /app

RUN mkdir -p \
    /app/data \
    /app/uploads \
    /app/logs
