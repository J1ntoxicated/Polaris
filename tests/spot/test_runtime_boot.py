import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from invasion.spot.runtime import Runtime


@pytest.mark.asyncio
async def test_runtime_starts_ws_task():
    rt = Runtime(dry_run=False)
    rt.boot()
    with patch("invasion.spot.ws_feed_spot.OKXSpotWSFeed.run",
                new=AsyncMock()) as mock_run:
        # one tick + stop
        async def _stopper():
            await asyncio.sleep(0.05)
            rt._stop()
        await asyncio.gather(rt.run_async(), _stopper())
    mock_run.assert_called_once()
