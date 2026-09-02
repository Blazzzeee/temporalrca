# Three-VM acceptance

1. Copy `inventory.example.yml` to `inventory.yml`, set the three existing Ubuntu 24.04 addresses, set `temporalrca_server_url` to VM1's reachable address (the example uses `192.0.2.11`), and set a long enrollment token. Run Ansible through the repository's Compose tooling:
   `docker compose --profile deployment run --rm ansible ansible-playbook -i deployment/ansible/inventory.yml deployment/ansible/site.yml`.
2. Confirm all three agents are connected in the fleet view and that VM2 has three systemd workers while VM1/VM3 have the Compose workloads.
3. Run `verify.py --url http://vm1:8000` for API topology and telemetry checks. The default process assertion is one active process record per host; pass `--min-processes-per-host N` when a deployment has a documented higher workload requirement.
4. Stop the central Compose project for ten minutes, restart it, and rerun `verify.py --require-recovery`. No agent gap event is acceptable while its 512 MiB/24-hour spool remained within capacity.
5. Run every allowlisted experiment in `experiment-runner/scenarios.example.yml`, confirm synchronized fault markers, then rerun verification with the completed run ID: `verify.py --url http://vm1:8000 --experiment-id <run-id>`. This downloads the export and verifies every Parquet manifest checksum.
