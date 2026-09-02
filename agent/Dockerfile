FROM python:3.12-slim AS base
RUN useradd --system --home-dir /var/lib/temporalrca-agent --create-home temporalrca-agent \
    && apt-get update \
    && apt-get install -y --no-install-recommends libcap2-bin \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/agent
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[postgres,redis]'

FROM base AS runtime
RUN setcap cap_dac_read_search,cap_sys_ptrace=eip /usr/local/bin/python3.12
USER temporalrca-agent
ENTRYPOINT ["temporalrca-agent"]
CMD ["--config", "/etc/temporalrca-agent/config.toml", "run"]

FROM base AS test
RUN pip install --no-cache-dir '.[test,postgres,redis]'
COPY tests ./tests
ENTRYPOINT []
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
