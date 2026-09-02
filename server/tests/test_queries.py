from datetime import datetime, timedelta, timezone

from temporalrca_server.api import select_bucket_seconds


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_bucket_selection_honors_max_points():
    assert select_bucket_seconds(START, START + timedelta(minutes=15), 900) == 1
    assert select_bucket_seconds(START, START + timedelta(hours=1), 300) == 15
    assert select_bucket_seconds(START, START + timedelta(days=1), 300) == 300


def test_bucket_selection_uses_largest_for_oversized_ranges():
    assert select_bucket_seconds(START, START + timedelta(days=365), 10) == 86400
