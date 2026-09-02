FROM docker:27-cli
RUN apk add --no-cache python3
WORKDIR /workspace
ENTRYPOINT ["python3", "deployment/benchmark/run.py"]
