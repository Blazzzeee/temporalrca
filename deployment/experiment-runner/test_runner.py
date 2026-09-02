import pytest

from runner import checked_int, start_fault


def test_bounds_are_enforced():
    with pytest.raises(ValueError):
        checked_int({"workers": 99}, "workers", 1, 1, 8)


@pytest.mark.asyncio
async def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError, match="not allowlisted"):
        await start_fault("shell", {}, 1)
