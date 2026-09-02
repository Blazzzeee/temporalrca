from datetime import datetime, timezone
from uuid import uuid4

from temporalrca_contracts.models import TelemetryEvent
from temporalrca_server.ingestion import canonical_digest, series_fingerprint


def test_digest_ignores_json_key_order():
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_series_fingerprint_is_stable_and_attribute_sensitive():
    base = dict(
        event_id=uuid4(), timestamp=datetime.now(timezone.utc),
        observed_timestamp=datetime.now(timezone.utc), sequence=1,
        source_type="system", signal_type="metric", name="host.cpu", value=1,
    )
    first = TelemetryEvent(**base, attributes={"cpu": "0", "mode": "user"})
    reordered = TelemetryEvent(**base, attributes={"mode": "user", "cpu": "0"})
    changed = TelemetryEvent(**base, attributes={"cpu": "1", "mode": "user"})
    host = str(uuid4())
    assert series_fingerprint(first, host) == series_fingerprint(reordered, host)
    assert series_fingerprint(first, host) != series_fingerprint(changed, host)
