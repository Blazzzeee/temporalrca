# Sampling benchmark

Start the complete Compose lab, then run:

```bash
python deployment/benchmark/run.py --duration 300 --repetitions 3
```

The harness covers the required 500 ms, 1 s, 2 s, and 5 s sampling periods and records each repetition's agent CPU, RSS, network/block I/O, and server counter deltas to CSV. Keep workload scale fixed between runs. The server counters provide ingestion volume, rejection/loss, and latency data; use the agent spool gauges to calculate outage growth and recovery time.

