"""Gateway logic with a scripted fake provider: no network, no real sleeping, deterministic."""

from collections.abc import Sequence
from datetime import timedelta

import pytest

from app.gateway.cache import CachedAnswer, CachePolicy, cache_key
from app.gateway.gateway import Action, GatewayConfig, LLMGateway, backoff_delay, classify
from app.gateway.types import (
    AllModelsFailedError,
    AuthError,
    BadRequestError,
    CallRecord,
    ChatMessage,
    Completion,
    ConnectionFailedError,
    LLMTimeoutError,
    ModelUnavailableError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitedError,
    ServerError,
)

MAX_DELAY = 20.0
Outcome = Completion | ProviderError


class FakeProvider:
    """Plays back a script: model -> list of outcomes, one per call. Records every call."""

    name = "fake"

    def __init__(self, script: dict[str, Sequence[Outcome]]) -> None:
        self.script = {m: list(o) for m, o in script.items()}
        self.calls: list[str] = []

    async def complete(
        self, model: str, messages: list[ChatMessage], *, max_tokens: int, temperature: float
    ) -> Completion:
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        pass


class FakeClock:
    """Time only advances when the gateway 'sleeps'."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def ok(model: str = "a") -> Completion:
    return Completion(text=f"answer from {model}", model=model, tokens_in=10, tokens_out=5)


def make(
    script: dict[str, Sequence[Outcome]], *, max_attempts: int = 4, deadline: float = 120.0
) -> tuple[LLMGateway, FakeProvider, FakeClock]:
    provider, clock = FakeProvider(script), FakeClock()
    config = GatewayConfig(
        models=list(script), max_delay=MAX_DELAY, max_attempts=max_attempts, deadline=deadline
    )
    gateway = LLMGateway(provider, config, sleep=clock.sleep, clock=clock, rng=lambda: 0.5)
    return gateway, provider, clock


MSG = [ChatMessage(role="user", content="hi")]


# ------------------------------------------------------------------ classify (your TODO)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMTimeoutError("t"), Action.RETRY_SAME),
        (ConnectionFailedError("c"), Action.RETRY_SAME),
        (ServerError("s", status=502), Action.RETRY_SAME),
        (RateLimitedError("r", status=429), Action.RETRY_SAME),  # no Retry-After
        (RateLimitedError("r", status=429, retry_after=5), Action.RETRY_SAME),
        (RateLimitedError("r", status=429, retry_after=MAX_DELAY), Action.RETRY_SAME),  # boundary
        (RateLimitedError("r", status=429, retry_after=600), Action.NEXT_MODEL),
        (ModelUnavailableError("m", status=404), Action.NEXT_MODEL),
        (QuotaExhaustedError("q", status=429), Action.FAIL),  # account-wide: all models fail
        (BadRequestError("b", status=400), Action.FAIL),
        (AuthError("a", status=401), Action.FAIL),
        (ProviderError("something new"), Action.FAIL),  # unknown -> fail closed
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, ProviderError) else str(v),
)
def test_classify(error: ProviderError, expected: Action) -> None:
    assert classify(error, MAX_DELAY) is expected


# ------------------------------------------------------------------ backoff


def test_backoff_grows_exponentially_with_jitter() -> None:
    mid = [backoff_delay(r, None, 1.0, 20.0, rng=lambda: 0.5) for r in (1, 2, 3, 4, 5, 6)]
    assert mid == [0.75, 1.5, 3.0, 6.0, 12.0, 15.0]  # cap/2 + 0.5*cap/2; capped at 20
    assert backoff_delay(3, None, 1.0, 20.0, rng=lambda: 0.0) == 2.0  # jitter range: [cap/2, cap]
    assert backoff_delay(3, None, 1.0, 20.0, rng=lambda: 1.0) == 4.0


def test_backoff_prefers_retry_after_but_caps_it() -> None:
    assert backoff_delay(1, 7.0, 1.0, 20.0, rng=lambda: 0.5) == 7.0
    assert backoff_delay(1, 90.0, 1.0, 20.0, rng=lambda: 0.5) == 20.0


# ------------------------------------------------------------------ whole gateway


async def test_first_model_answers() -> None:
    gw, provider, clock = make({"a": [ok("a")], "b": [ok("b")]})
    r = await gw.chat(MSG)
    assert r.text == "answer from a" and provider.calls == ["a"] and clock.sleeps == []
    assert [x.error for x in r.attempts] == [None]


async def test_transient_error_is_retried_on_the_same_model() -> None:
    gw, provider, clock = make({"a": [ServerError("boom", status=503), ok("a")], "b": []})
    r = await gw.chat(MSG)
    assert provider.calls == ["a", "a"]
    assert clock.sleeps == [0.75]  # backoff before the retry
    assert r.attempts[0].error == "ServerError 503" and r.attempts[1].error is None


async def test_unavailable_model_falls_back_without_waiting() -> None:
    gw, provider, clock = make({"a": [ModelUnavailableError("gone", status=404)], "b": [ok("b")]})
    r = await gw.chat(MSG)
    assert provider.calls == ["a", "b"] and r.model == "b" and clock.sleeps == []


async def test_retries_per_model_are_capped_then_falls_back() -> None:
    errs = [LLMTimeoutError("t"), LLMTimeoutError("t"), LLMTimeoutError("t")]
    gw, provider, _ = make({"a": errs, "b": [ok("b")]}, max_attempts=10)
    r = await gw.chat(MSG)
    assert provider.calls == ["a", "a", "a", "b"]  # 1 try + 2 retries, then next model
    assert r.model == "b"


async def test_long_retry_after_skips_to_next_model() -> None:
    gw, provider, clock = make(
        {"a": [RateLimitedError("slow down", status=429, retry_after=3600)], "b": [ok("b")]}
    )
    await gw.chat(MSG)
    assert provider.calls == ["a", "b"] and clock.sleeps == []  # we don't wait an hour


@pytest.mark.parametrize(
    "error",
    [
        BadRequestError("prompt too long", status=400),
        AuthError("bad key", status=401),
        QuotaExhaustedError("free daily limit", status=429),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_our_side_errors_fail_fast_without_touching_other_models(
    error: ProviderError,
) -> None:
    gw, provider, _ = make({"a": [error], "b": [ok("b")]})
    with pytest.raises(type(error)):
        await gw.chat(MSG)
    assert provider.calls == ["a"]  # not a single wasted request on "b"


async def test_total_attempt_budget_caps_requests() -> None:
    """3 models x (1 + 2 retries) would be 9 requests; the budget stops at 4."""
    errs = [ServerError("x", status=500)] * 3
    gw, provider, _ = make({"a": errs, "b": list(errs), "c": list(errs)}, max_attempts=4)
    with pytest.raises(AllModelsFailedError, match="attempt budget"):
        await gw.chat(MSG)
    assert len(provider.calls) == 4


async def test_deadline_stops_retrying() -> None:
    errs = [RateLimitedError("r", status=429, retry_after=15)] * 3
    gw, provider, clock = make({"a": errs, "b": [ok("b")]}, deadline=20, max_attempts=10)
    with pytest.raises(AllModelsFailedError, match="deadline"):
        await gw.chat(MSG)
    assert clock.sleeps == [15]  # a 2nd 15 s wait would cross the 20 s deadline: stop instead
    assert provider.calls == ["a", "a"]


async def test_all_models_failing_reports_every_attempt() -> None:
    gw, _, _ = make(
        {
            "a": [ModelUnavailableError("x", status=404)],
            "b": [ModelUnavailableError("y", status=404)],
        }
    )
    with pytest.raises(AllModelsFailedError) as exc:
        await gw.chat(MSG)
    assert [a.model for a in exc.value.attempts] == ["a", "b"]
    assert "ModelUnavailableError 404" in str(exc.value)


def test_empty_model_list_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_MODELS"):
        LLMGateway(FakeProvider({}), GatewayConfig(models=[]))


# ------------------------------------------------------------------ call recording (2.2)


class FakeRecorder:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[CallRecord] = []
        self.fail = fail

    async def record(self, call: CallRecord) -> None:
        if self.fail:
            raise RuntimeError("database down")
        self.records.append(call)


def make_recorded(
    script: dict[str, Sequence[Outcome]], recorder: FakeRecorder
) -> tuple[LLMGateway, FakeProvider]:
    provider, clock = FakeProvider(script), FakeClock()
    gateway = LLMGateway(
        provider,
        GatewayConfig(models=list(script)),
        sleep=clock.sleep,
        clock=clock,
        rng=lambda: 0.5,
        recorder=recorder,
    )
    return gateway, provider


async def test_every_attempt_of_a_call_is_recorded_with_one_call_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.gateway.gateway.prompt_hash", lambda *a, **k: "h" * 64)
    rec = FakeRecorder()
    gw, _ = make_recorded({"a": [ServerError("x", status=503)], "b": [ok("b")]}, rec)
    gw.config = GatewayConfig(models=["a", "b"], max_retries_per_model=0)

    response = await gw.chat(MSG, purpose="test")

    [record] = rec.records
    assert record.call_id == response.call_id
    assert (record.purpose, record.provider, record.prompt_hash) == ("test", "fake", "h" * 64)
    first, second = record.attempts
    assert (first.model, first.ok, first.error_type, first.http_status) == (
        "a",
        False,
        "ServerError",
        503,
    )
    assert (second.model, second.ok, second.tokens_in, second.tokens_out) == ("b", True, 10, 5)


async def test_failed_calls_are_recorded_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rows you most want to see later are the ones where everything failed."""
    monkeypatch.setattr("app.gateway.gateway.prompt_hash", lambda *a, **k: "h" * 64)
    rec = FakeRecorder()
    gw, _ = make_recorded({"a": [AuthError("bad key", status=401)]}, rec)
    with pytest.raises(AuthError):
        await gw.chat(MSG)
    assert [a.error_type for a in rec.records[0].attempts] == ["AuthError"]


async def test_a_broken_recorder_never_breaks_the_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("app.gateway.gateway.prompt_hash", lambda *a, **k: "h" * 64)
    gw, _ = make_recorded({"a": [ok("a")]}, FakeRecorder(fail=True))
    response = await gw.chat(MSG)
    assert response.text == "answer from a"  # the user still gets their answer...
    assert "Failed to record LLM call" in caplog.text  # ...and the failure is loud in the logs


# ------------------------------------------------------------------ prompt cache (2.3)


class FakeCache:
    """In-memory PromptCache. `broken` simulates the cache DB being down."""

    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, CachedAnswer] = {}
        self.gets = self.puts = 0
        self.broken = broken

    async def get(self, key: str, ttl: timedelta) -> CachedAnswer | None:
        self.gets += 1
        if self.broken:
            raise RuntimeError("cache db down")
        return self.store.get(key)

    async def put(
        self, key: str, prompt_hash: str, scope: str, answer: CachedAnswer, ttl: timedelta
    ) -> None:
        self.puts += 1
        if self.broken:
            raise RuntimeError("cache db down")
        self.store[key] = answer


def make_cached(
    script: dict[str, Sequence[Outcome]], cache: FakeCache, recorder: FakeRecorder | None = None
) -> tuple[LLMGateway, FakeProvider]:
    provider, clock = FakeProvider(script), FakeClock()
    gateway = LLMGateway(
        provider,
        GatewayConfig(models=list(script)),
        sleep=clock.sleep,
        clock=clock,
        rng=lambda: 0.5,
        recorder=recorder,
        cache=cache,
    )
    return gateway, provider


def stop(text: str = "cached?", model: str = "a") -> Completion:
    return Completion(text=text, model=model, tokens_in=10, tokens_out=5, finish_reason="stop")


async def test_second_identical_request_is_a_cache_hit_with_zero_tokens() -> None:
    cache, rec = FakeCache(), FakeRecorder()
    gw, provider = make_cached({"a": [stop("first answer")]}, cache, rec)

    first = await gw.chat(MSG, purpose="dev")
    second = await gw.chat(MSG, purpose="dev")

    assert provider.calls == ["a"]  # only ONE real request
    assert (first.cached, second.cached) == (False, True)
    assert second.text == "first answer"
    assert (second.tokens_in, second.tokens_out) == (0, 0)  # nothing spent
    assert [r.cached for r in rec.records] == [False, True]  # the hit is logged too


@pytest.mark.parametrize(
    "completion",
    [
        Completion(
            text="The bearing is", model="a", tokens_in=1, tokens_out=1, finish_reason="length"
        ),  # truncated
        Completion(text="   ", model="a", tokens_in=1, tokens_out=1, finish_reason="stop"),  # empty
        Completion(text="ok", model="a", tokens_in=1, tokens_out=1, finish_reason=None),  # unknown
    ],
    ids=["truncated", "blank", "no-finish-reason"],
)
async def test_unclean_answers_are_never_cached(completion: Completion) -> None:
    cache = FakeCache()
    gw, _ = make_cached({"a": [completion]}, cache)
    await gw.chat(MSG)
    assert cache.store == {}  # replaying a bad answer forever would be invisible


async def test_errors_are_never_cached() -> None:
    cache = FakeCache()
    gw, _ = make_cached({"a": [AuthError("bad key", status=401)]}, cache)
    with pytest.raises(AuthError):
        await gw.chat(MSG)
    assert cache.puts == 0


async def test_policy_off_skips_the_cache_entirely() -> None:
    cache = FakeCache()
    gw, provider = make_cached({"a": [stop(), stop()]}, cache)
    await gw.chat(MSG, cache=CachePolicy.OFF)
    await gw.chat(MSG, cache=CachePolicy.OFF)
    assert (cache.gets, cache.puts, len(provider.calls)) == (0, 0, 2)


def test_per_model_keys_separate_models_shared_keys_dont() -> None:
    """Quiz 2.2: comparing models must not compare the cache with itself."""
    shared_a = cache_key("h", CachePolicy.SHARED, ["model-a"])
    shared_b = cache_key("h", CachePolicy.SHARED, ["model-b"])
    per_a = cache_key("h", CachePolicy.PER_MODEL, ["model-a"])
    per_b = cache_key("h", CachePolicy.PER_MODEL, ["model-b"])
    assert shared_a == shared_b
    assert per_a != per_b and per_a != shared_a
    assert cache_key("h", CachePolicy.OFF, ["model-a"]) is None


async def test_a_broken_cache_is_a_miss_not_a_failure(caplog: pytest.LogCaptureFixture) -> None:
    gw, provider = make_cached({"a": [stop("live answer")]}, FakeCache(broken=True))
    response = await gw.chat(MSG)
    assert response.text == "live answer" and provider.calls == ["a"]
    assert "Prompt cache lookup failed" in caplog.text
    assert "Prompt cache store failed" in caplog.text
