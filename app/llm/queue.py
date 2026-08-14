import asyncio
import logging
import time
from collections import deque

from app.llm.client import InitialDoc, LLMTransientError, SectionUpdate

logger = logging.getLogger(__name__)

DEFAULT_RETRIES = 1  # NFR: retry once on transient failure
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_RATE_PER_MINUTE = 10


class LLMQueue:
    """Serialize and throttle shared-key LLM calls.

    A single platform NVIDIA key is shared across orgs on a free-tier rate
    limit, so concurrent scheduler jobs must not burst past the ceiling.
    """

    def __init__(
        self,
        rate_per_minute: int = DEFAULT_RATE_PER_MINUTE,
        max_concurrency: int = 1,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        self.rate_per_minute = max(1, rate_per_minute)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._retries = max(0, retries)
        self._backoff_seconds = backoff_seconds
        self._call_times: deque[float] = deque()

    async def _wait_for_rate_slot(self) -> None:
        while True:
            now = time.monotonic()
            self._call_times = deque(t for t in self._call_times if t > now - 60)
            if len(self._call_times) < self.rate_per_minute:
                self._call_times.append(now)
                return
            await asyncio.sleep(1.0)

    async def run(self, coro_factory, *args, **kwargs):
        """Serialized, rate-limited, retrying execution of a coroutine factory."""
        async with self._semaphore:
            await self._wait_for_rate_slot()
            last_error: Exception | None = None
            for attempt in range(self._retries + 1):
                try:
                    return await coro_factory(*args, **kwargs)
                except LLMTransientError as e:
                    last_error = e
                    logger.warning("transient LLM error (attempt %s): %s", attempt + 1, e)
                    if attempt < self._retries:
                        await asyncio.sleep(self._backoff_seconds * (2**attempt))
            raise last_error

    async def generate_section_update(
        self,
        client,
        context_md: str,
        diff_payload: dict,
        section_key_hint: str | None = None,
    ) -> tuple[SectionUpdate, int]:
        return await self.run(
            client.generate_section_update,
            context_md,
            diff_payload,
            section_key_hint,
        )

    async def generate_initial_doc(
        self,
        client,
        source_content: str,
        source_type: str,
        source_name: str,
    ) -> tuple[InitialDoc, int]:
        return await self.run(
            client.generate_initial_doc,
            source_content,
            source_type,
            source_name,
        )
