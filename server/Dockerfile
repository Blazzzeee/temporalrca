FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY shared /shared
COPY server /app
RUN pip install --no-cache-dir /shared /app

FROM base AS test
RUN pip install --no-cache-dir "/app[test]"
CMD ["pytest", "-q"]

FROM base AS runtime
RUN useradd --system --uid 10001 --home-dir /nonexistent temporalrca
RUN mkdir -p /exports && chown temporalrca:temporalrca /exports
USER temporalrca
EXPOSE 8000
CMD ["uvicorn", "temporalrca_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
