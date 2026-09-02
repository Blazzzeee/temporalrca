from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from temporalrca_contracts.models import TelemetryEvent


def metric(**changes):
    values = {
        "event_id": uuid4(), "timestamp": datetime.now(timezone.utc),
        "observed_timestamp": datetime.now(timezone.utc), "sequence": 1,
        "source_type": "system", "signal_type": "metric", "name": "host.cpu.utilization",
        "value": 12.5, "unit": "percent", "attributes": {"cpu": "0"},
    }
    values.update(changes)
    return values


def test_metric_requires_value():
    with pytest.raises(ValidationError, match="metric events require value"):
        TelemetryEvent.model_validate(metric(value=None))


def test_process_source_requires_process_identity():
    with pytest.raises(ValidationError, match="process events require process_id"):
        TelemetryEvent.model_validate(metric(source_type="process"))


def test_timestamps_require_timezone():
    with pytest.raises(ValidationError, match="timezone"):
        TelemetryEvent.model_validate(metric(timestamp=datetime.now()))


def test_attributes_are_bounded():
    with pytest.raises(ValidationError, match="at most 64"):
        TelemetryEvent.model_validate(metric(attributes={str(i): i for i in range(65)}))


def test_nested_json_attributes_are_supported():
    event = TelemetryEvent.model_validate(metric(attributes={"labels": ["api", "critical"], "resource": {"zone": "a"}}))
    assert event.attributes["resource"] == {"zone": "a"}
