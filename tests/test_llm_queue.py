import asyncio

import pytest

from app.llm.client import LLMTransientError, SectionUpdate
from app.llm.queue import LLMQueue


class _HandlerClient:
    def __init__(self, handler) -> None:
        self._handler = handler

    async def generate_section_update(self, context_md: str, diff_payload: dict, hint: str | None = None):
        return await self._handler(context_md, diff_payload, hint)


@pytest.mark.anyio
async def test_llm_queue_serializes_concurrent_calls() -> None:
    counter = {"active": 0, "max_active": 0, "calls": 0}

    async def handler(context_md, diff_payload, hint):
        counter["active"] += 1
        counter["max_active"] = max(counter["max_active"], counter["active"])
        await asyncio.sleep(0.01)
        counter["active"] -= 1
        counter["calls"] += 1
        return SectionUpdate(section_key="S", new_content="body", reason="r"), 10

    queue = LLMQueue(max_concurrency=1, rate_per_minute=100)
    client = _HandlerClient(handler)

    await asyncio.gather(
        *[queue.generate_section_update(client, "", {}) for _ in range(5)]
    )

    assert counter["calls"] == 5
    assert counter["max_active"] == 1


@pytest.mark.anyio
async def test_llm_retries_once_on_transient_error() -> None:
    calls = {"count": 0}

    async def flaky_handler(context_md, diff_payload, hint):
        calls["count"] += 1
        if calls["count"] == 1:
            raise LLMTransientError("rate limited")
        return SectionUpdate(section_key="S", new_content="body", reason="r"), 7

    queue = LLMQueue(retries=1, backoff_seconds=0.01, rate_per_minute=100)
    update, tokens = await queue.generate_section_update(
        _HandlerClient(flaky_handler), "", {}
    )

    assert calls["count"] == 2
    assert update.section_key == "S"
    assert tokens == 7


@pytest.mark.anyio
async def test_llm_exhausts_retries_and_raises() -> None:
    async def always_fail(context_md, diff_payload, hint):
        raise LLMTransientError("still failing")

    queue = LLMQueue(retries=1, backoff_seconds=0.01, rate_per_minute=100)
    with pytest.raises(LLMTransientError):
        await queue.generate_section_update(_HandlerClient(always_fail), "", {})
