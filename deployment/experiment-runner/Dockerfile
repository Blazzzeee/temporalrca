FROM python:3.12-slim
WORKDIR /runner
RUN apt-get update && apt-get install -y --no-install-recommends fio iproute2 postgresql-client redis-tools stress-ng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runner.py scenarios.example.yml ./
ENTRYPOINT ["python", "runner.py"]
CMD ["scenarios.example.yml"]

