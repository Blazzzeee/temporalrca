from datetime import datetime, timezone
from uuid import uuid4

from temporalrca_contracts.models import InventoryResponse


def test_inventory_response_exposes_server_authoritative_ids():
    ids = {"api": uuid4()}
    response = InventoryResponse(
        inventory_watermark=2, lease_expires_at=datetime.now(timezone.utc),
        service_ids=ids, process_ids={}, container_ids={}, dependency_ids={},
    )
    assert response.service_ids["api"] == ids["api"]
