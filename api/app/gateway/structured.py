"""Structured output (5.2a): a pydantic model out of a free-text LLM answer, via OUR gateway.

Why not `response_format: json_schema` or ChatOpenAI.with_structured_output: support for that
varies per OpenRouter model, and the free models rotate (2.1). A schema in the prompt + strict
validation + ONE repair round works with any model, and every attempt goes through the gateway
(fallback list, call log, prompt cache).

    triage = await chat_structured(gateway, messages, TriageOut, purpose="agent.triage")
    triage.value   # the validated model
    triage.calls   # requests made (1 or 2): the agent's budget counts these
"""

import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.gateway.cache import CachePolicy
from app.gateway.types import ChatMessage

MAX_REPAIRS = 1  # one corrected attempt; a model that fails twice won't succeed on the third


class ChatResult(Protocol):
    @property
    def text(self) -> str: ...


class ChatModel(Protocol):
    """What chat_structured needs: LLMGateway fits, and so does a scripted fake in tests."""

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = ...,
        temperature: float = ...,
        purpose: str = ...,
        cache: CachePolicy = ...,
    ) -> ChatResult: ...


class StructuredOutputError(Exception):
    """The model didn't produce valid JSON for the schema, even after a repair round."""


@dataclass(frozen=True)
class Structured[T: BaseModel]:
    value: T
    calls: int


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """The JSON object in a model answer: inside ```json fences if present, else from the first
    "{" to the last "}". Free models like to wrap JSON in prose or fences."""
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object found")
    return text[start : end + 1]


def schema_instruction(model: type[BaseModel]) -> str:
    schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
    return (
        "Reply with ONE JSON object and nothing else (no prose, no markdown), valid against this "
        f"JSON Schema:\n{schema}"
    )


async def chat_structured[T: BaseModel](
    gateway: ChatModel,
    messages: list[ChatMessage],
    model: type[T],
    *,
    purpose: str,
    max_tokens: int = 1200,
) -> Structured[T]:
    """Ask, parse, validate; on failure ask ONCE more, with the error. Raises
    StructuredOutputError."""
    convo = [*messages, ChatMessage(role="user", content=schema_instruction(model))]
    error = ""
    for attempt in range(1 + MAX_REPAIRS):
        answer = await gateway.chat(convo, max_tokens=max_tokens, temperature=0.0, purpose=purpose)
        try:
            return Structured(model.model_validate_json(extract_json(answer.text)), attempt + 1)
        except (ValueError, ValidationError) as e:  # ValidationError is a ValueError, kept explicit
            error = str(e)[:500]
            convo = [
                *convo,
                ChatMessage(role="assistant", content=answer.text),
                ChatMessage(
                    role="user",
                    content=f"That was not valid: {error}\nReply again with ONLY the JSON object.",
                ),
            ]
    raise StructuredOutputError(f"{purpose}: no valid {model.__name__} after repair: {error}")
