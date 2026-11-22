
FROM python:3.12-slim AS build

WORKDIR /build

COPY requirements.txt .

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True

WORKDIR /app

COPY --from=build /opt/venv /opt/venv

COPY . /app/backend/

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
