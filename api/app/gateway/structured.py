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
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.gateway.cache import CachePolicy
from app.gateway.types import ChatMessage, LLMError

MAX_REPAIRS = 1  # one corrected attempt; a model that fails twice won't succeed on the third


class ChatResult(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def provider_requests(self) -> int: ...

    @property
    def tokens(self) -> int: ...

    @property
    def model(self) -> str: ...  # the model that answered (after fallbacks)


@dataclass
class Spend:
    """A meter for LLM use (5.4b): chat_structured adds to it after EVERY gateway call, also when
    the call fails or the repair round does, so a node can bill what a call really cost even
    when it ends in an exception. calls = our gateway calls; attempts = provider requests (the
    quota: one call can be up to LLM_MAX_ATTEMPTS of them); tokens = in + out."""

    calls: int = 0
    attempts: int = 0
    tokens: int = 0
    # who answered each successful call, in order (8.4: an eval judge's verdict must say which
    # model gave it; a fallback model may never have been calibrated)
    models: list[str] = field(default_factory=list)

    def add(self, result: ChatResult) -> None:
        self.calls += 1
        self.models.append(result.model)
        self.attempts += result.provider_requests
        self.tokens += result.tokens

    def add_failure(self, error: LLMError) -> None:
        # A failed call carries its attempts (gateway); unknown means at least one was made.
        self.calls += 1
        self.attempts += len(error.attempts) or 1


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


# The temperature of EVERY structured call, i.e. all four agent LLM calls and the eval judge:
# deterministic-ish output for a schema. The eval manifest records this value (8.6b); before,
# it recorded the gateway's DEFAULT_TEMPERATURE (0.2), which no agent call uses.
STRUCTURED_TEMPERATURE = 0.0


async def chat_structured[T: BaseModel](
    gateway: ChatModel,
    messages: list[ChatMessage],
    model: type[T],
    *,
    purpose: str,
    max_tokens: int = 1200,
    spend: Spend | None = None,
    cache: CachePolicy = CachePolicy.SHARED,
) -> Structured[T]:
    """Ask, parse, validate; on failure ask ONCE more, with the error. Raises
    StructuredOutputError. Every gateway call, failed or not, is added to `spend`.
    `cache`: the prompt-cache policy, passed to the gateway as is (8.6b: the judge needs
    PER_MODEL; the agent keeps the default)."""
    spend = spend if spend is not None else Spend()
    convo = [*messages, ChatMessage(role="user", content=schema_instruction(model))]
    error = ""
    for attempt in range(1 + MAX_REPAIRS):
        try:
            answer = await gateway.chat(
                convo,
                max_tokens=max_tokens,
                temperature=STRUCTURED_TEMPERATURE,
                purpose=purpose,
                cache=cache,
            )
        except LLMError as e:
            spend.add_failure(e)
            raise
        spend.add(answer)
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
