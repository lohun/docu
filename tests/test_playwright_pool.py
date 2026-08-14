import asyncio
import pytest

from app.scraper.worker_pool import ScraperWorkerPool
from app.security import SSRFError


@pytest.mark.anyio
async def test_worker_pool_concurrency_limit() -> None:
    pool = ScraperWorkerPool(max_concurrency=3)
    active_count = 0
    max_observed = 0

    async def dummy_task() -> None:
        nonlocal active_count, max_observed
        async with pool.acquire():
            active_count += 1
            max_observed = max(max_observed, active_count)
            await asyncio.sleep(0.05)
            active_count -= 1

    tasks = [dummy_task() for _ in range(10)]
    await asyncio.gather(*tasks)

    assert max_observed <= 3


@pytest.mark.anyio
async def test_worker_pool_ssrf_blocking() -> None:
    pool = ScraperWorkerPool(max_concurrency=3)
    with pytest.raises(SSRFError):
        await pool.scrape_url("http://169.254.169.254/latest/meta-data/")
