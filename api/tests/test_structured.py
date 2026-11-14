"""Structured output via our gateway: schema in the prompt, strict validation, one repair round."""

import pytest
from pydantic import BaseModel

from app.gateway.cache import CachePolicy
from app.gateway.structured import (
    StructuredOutputError,
    chat_structured,
    extract_json,
    schema_instruction,
)
from app.gateway.types import ChatMessage, Completion
from app.testing import ScriptedChat
from tests.test_gateway import make


class Out(BaseModel):
    machine: str
    score: int


MSG = [ChatMessage(role="user", content="which machine?")]


@pytest.mark.parametrize(
    "text",
    [
        '{"machine": "M-08", "score": 3}',
        'Sure! Here it is:\n```json\n{"machine": "M-08", "score": 3}\n```\nHope that helps.',
        'The answer is {"machine": "M-08", "score": 3} as requested.',
    ],
)
def test_extract_json_tolerates_fences_and_prose(text: str) -> None:
    assert Out.model_validate_json(extract_json(text)) == Out(machine="M-08", score=3)


def test_schema_instruction_names_the_fields() -> None:
    text = schema_instruction(Out)
    assert '"machine"' in text and '"score"' in text and "ONE JSON object" in text


async def test_valid_answer_needs_one_call() -> None:
    chat = ScriptedChat('{"machine": "M-08", "score": 3}')
    out = await chat_structured(chat, MSG, Out, purpose="t")
    assert (out.value, out.calls) == (Out(machine="M-08", score=3), 1)


async def test_invalid_answer_gets_one_repair_with_the_error() -> None:
    chat = ScriptedChat('{"machine": "M-08", "score": "three"}', '{"machine": "M-08", "score": 3}')
    out = await chat_structured(chat, MSG, Out, purpose="t")
    assert out.calls == 2
    repair = chat.conversations[1][-1].content
    assert "not valid" in repair and "score" in repair  # the model is told WHAT was wrong


async def test_two_invalid_answers_raise() -> None:
    chat = ScriptedChat("no json here", "still none")
    with pytest.raises(StructuredOutputError, match="no valid Out"):
        await chat_structured(chat, MSG, Out, purpose="t")


async def test_through_the_real_gateway() -> None:
    """LLMGateway satisfies the ChatModel protocol, and structured calls take the normal path."""
    reply = Completion('{"machine": "M-09", "score": 1}', "m:free", 10, 5, finish_reason="stop")
    gateway, provider, _ = make({"m:free": [reply]})
    out = await chat_structured(gateway, MSG, Out, purpose="t")
    assert out.value.machine == "M-09" and provider.calls == ["m:free"]
    assert CachePolicy.SHARED  # the default policy: a repeated question costs no request
