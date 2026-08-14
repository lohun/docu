import logging

import pytest

from app.llm.client import (
    LLMClient,
    LLMInvalidResponseError,
    LLMTransientError,
)
from app.logging_conf import RedactFilter


class _FakeCompletions:
    def __init__(self, content: str, tokens: int = 42, error: Exception | None = None) -> None:
        self._content = content
        self._tokens = tokens
        self._error = error

    async def create(self, **kwargs) -> object:
        if self._error is not None:
            raise self._error

        class _Message:
            content = self._content

        class _Choice:
            message = _Message()

        class _Usage:
            total_tokens = self._tokens

        class _Response:
            choices = [_Choice()]
            usage = _Usage()

        return _Response()


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = _FakeChat(completions)


def _client_with(completions: _FakeCompletions) -> LLMClient:
    client = LLMClient(api_key="nvapi-test-key-1234567890")
    client._client = _FakeClient(completions)  # type: ignore[assignment]
    return client


@pytest.mark.anyio
async def test_llm_returns_structured_section_update_json() -> None:
    completions = _FakeCompletions(
        '{"section_key": "Getting Started", '
        '"new_content": "Install the package and run it.", '
        '"reason": "added install step"}'
    )
    client = _client_with(completions)

    update, tokens = await client.generate_section_update("", {})
    assert update.section_key == "Getting Started"
    assert "Install the package" in update.new_content
    assert tokens == 42


@pytest.mark.anyio
async def test_llm_invalid_json_raises_contract_error() -> None:
    client = _client_with(_FakeCompletions("this is not json"))
    with pytest.raises(LLMInvalidResponseError):
        await client.generate_section_update("", {})


@pytest.mark.anyio
async def test_llm_transient_error_propagates_for_retry() -> None:
    client = _client_with(
        _FakeCompletions("", error=RuntimeError("upstream timeout"))
    )
    with pytest.raises(LLMTransientError):
        await client.generate_section_update("", {})


def test_log_redaction_filter_masks_nvapi_keys() -> None:
    record = logging.LogRecord(
        "test", logging.ERROR, "/path", 1, "failed with key nvapi-ABCDEFGHIJKLMNOP", (), None
    )
    RedactFilter().filter(record)
    assert "nvapi-" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()


def test_log_redaction_filter_masks_secrets_in_args() -> None:
    record = logging.LogRecord(
        "test", logging.ERROR, "/path", 1, "auth failed key=%s", ("nvapi-secretvalue123456",), None
    )
    RedactFilter().filter(record)
    assert "nvapi-secretvalue123456" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
