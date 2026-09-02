FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY demo-workload/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent /opt/temporalrca-agent-source
RUN pip install --no-cache-dir '/opt/temporalrca-agent-source[postgres,redis]'
COPY demo-workload/workload ./workload
COPY demo-workload/container_entrypoint.py /usr/local/bin/temporalrca-container
COPY config/agent.container.toml /etc/temporalrca-agent/container.toml

FROM base AS runtime
RUN useradd --system --uid 10001 workload \
    && mkdir -p /var/log/temporalrca /var/lib/temporalrca-agent /work \
    && chown -R workload:workload /app /var/log/temporalrca /var/lib/temporalrca-agent /work
USER workload
ENTRYPOINT ["python", "/usr/local/bin/temporalrca-container"]

FROM base AS test
RUN pip install --no-cache-dir pytest==8.4.1
COPY demo-workload/tests ./tests
ENTRYPOINT []
CMD ["python", "-m", "pytest", "-q"]
