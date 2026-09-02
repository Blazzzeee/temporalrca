from datetime import datetime, timezone

from temporalrca_server.partitions import floor_day, floor_hour


def test_partition_boundaries_are_utc():
    value = datetime.fromisoformat("2026-01-02T05:34:10+05:30")
    assert floor_hour(value) == datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert floor_day(value) == datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)
