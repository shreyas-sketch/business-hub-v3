"""
One interface, four engines (AI_PROVIDER = anthropic | gemini | openai | mock).
Every call returns (result, usage) so each run's token cost can be recorded and shown on Pulse.
`mock` returns deterministic, profile-based drafts so the whole hub works without a key.
"""
import asyncio
import json
import random
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from ..config import settings

# Current Claude models think before answering, and thinking counts towards max_tokens: this keeps room for it so
# the JSON answer itself is never cut off.
THINKING_HEADROOM = 4000
DEFAULT_MODEL = {"anthropic": "claude-haiku-5-5", "gemini": "gemini-2.5-flash", "openai": "gpt-4.1-mini"}


class AIError(Exception):
    """Raised when the engine fails; callers refund the run and show a plain message."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "mock"


def live() -> bool:
    return settings.ai_provider != "mock"


def _json(text: str) -> dict:
    clean = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", clean)
        if not m:
            raise AIError("The AI returned an unreadable answer")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise AIError("The AI returned an unreadable answer") from e


class Busy(AIError):
    """The engine said "too many requests" or "overloaded" (429 / 503 / 529): worth one more try after a short wait."""

    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class Down(AIError):
    """The engine can't be used just now (unreachable, a 5xx, a bad key or no credit): a backup engine, if there is one,
    takes the call."""


# A room of 200 owners pressing AI buttons at the same moment. The hub has a pool of engines: the main one (AI_PROVIDER), once
# per key, then any backups (AI_FALLBACK). At most AI_CONCURRENCY calls go out at once to each engine from each server process;
# the rest wait their turn. An engine that answers "slow down" rests for the wait it asks for and the call moves to the next
# engine; with every engine resting, the call waits for the first one back (AI_RETRIES more rounds at most).
BUSY_STATUS = (429, 503, 529)
DOWN_REST = 30.0     # seconds an engine that's down is left alone, when there's another engine to use
MAX_WAIT = 30.0


@dataclass
class Engine:
    provider: str
    key: str
    model: str
    rest_until: float = 0.0          # time.monotonic() before which this engine isn't asked
    sem: asyncio.Semaphore | None = None
    size: int = 0
    loop: object = None

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}" + (f" (key …{self.key[-4:]})" if len(self.key) > 8 else "")

    def gate(self) -> asyncio.Semaphore:
        size, loop = max(1, settings.ai_concurrency), asyncio.get_running_loop()
        if self.sem is None or self.size != size or self.loop is not loop:
            self.sem, self.size, self.loop = asyncio.Semaphore(size), size, loop
        return self.sem


_pool: dict = {"sig": None, "engines": [], "turn": 0}
_current: ContextVar[Engine | None] = ContextVar("ai_engine", default=None)


def _keys(provider: str) -> list[str]:
    one = {"anthropic": settings.anthropic_api_key, "gemini": settings.gemini_api_key, "openai": settings.openai_api_key}.get(provider, "")
    more = {"anthropic": settings.anthropic_api_keys, "gemini": settings.gemini_api_keys, "openai": settings.openai_api_keys}.get(provider, "")
    return list(dict.fromkeys(k.strip() for k in [one, *more.split(",")] if k.strip()))


def engines() -> list[Engine]:
    """The pool, main engine first. Rebuilt only when the settings change, so each engine's resting time is shared by every call."""
    sig = (settings.ai_provider, settings.ai_model, settings.ai_fallback, settings.anthropic_api_key, settings.anthropic_api_keys,
           settings.gemini_api_key, settings.gemini_api_keys, settings.openai_api_key, settings.openai_api_keys)
    if _pool["sig"] != sig:
        main = settings.ai_provider
        # the main engine always has a place, even without a key, so a missing key shows as the engine's own error
        pool = [Engine(main, k, settings.ai_model or DEFAULT_MODEL.get(main, "")) for k in _keys(main) or [""]]
        for item in (x.strip() for x in settings.ai_fallback.split(",") if x.strip()):
            name, _, model = item.partition(":")
            model = model.strip() or DEFAULT_MODEL.get(name, "")
            if name not in DEFAULT_MODEL or any(e.provider == name and e.model == model for e in pool):
                continue
            pool += [Engine(name, k, model) for k in _keys(name)]
        _pool.update(sig=sig, engines=pool, turn=0)
    return _pool["engines"]


def _next(pool: list[Engine]) -> Engine | None:
    """A free engine: the main engine's keys take turns; a backup only while every main key rests."""
    t = time.monotonic()
    main = [e for e in pool if e.provider == pool[0].provider and e.model == pool[0].model]
    _pool["turn"] += 1
    ordered = main[_pool["turn"] % len(main):] + main[:_pool["turn"] % len(main)] + pool[len(main):]
    return next((e for e in ordered if e.rest_until <= t), None)


def current_engine() -> Engine | None:
    """The engine the running call was given (the load test's simulated engine reads it)."""
    return _current.get()


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return min(float(r.headers.get("retry-after", "")), 30.0)
    except ValueError:
        return None


async def complete(system: str, prompt: str, mock, max_tokens: int = 1200, images: list[tuple[str, bytes]] | None = None) -> tuple[dict, Usage]:
    """`images`: optional [(media type, bytes)] the engine reads with the prompt (e.g. a photo of an invoice)."""
    if settings.ai_provider == "mock":
        return mock(), Usage()
    pool = engines()
    attempts, last = 0, None
    while True:
        eng = _next(pool)
        if eng is None:     # every engine is resting: wait for the first one back
            eng = min(pool, key=lambda e: e.rest_until)
            await asyncio.sleep(min(max(0.0, eng.rest_until - time.monotonic()), MAX_WAIT))
        attempts += 1
        try:
            async with eng.gate():
                token = _current.set(eng)
                try:
                    return await _call_once(eng.provider, system, prompt, mock, max_tokens, **({"images": images} if images else {}))
                finally:
                    _current.reset(token)
        except Busy as e:
            eng.rest_until = time.monotonic() + (e.wait if e.wait is not None else min(2 ** attempts, 8) + random.random())
            last = e
        except Down as e:
            if len(pool) == 1:
                raise
            eng.rest_until = time.monotonic() + DOWN_REST
            last = e
        if attempts >= len(pool) * (settings.ai_retries + 1):
            raise last


async def _call_once(provider: str, system: str, prompt: str, mock, max_tokens: int,
                     images: list[tuple[str, bytes]] | None = None) -> tuple[dict, Usage]:
    """One request to the engine `complete` picked from the pool. (`mock` is unused here; the load test swaps this function for a
    simulated engine.)"""
    import base64
    eng = _current.get()
    model = eng.model if eng else settings.ai_model or DEFAULT_MODEL.get(provider, "")
    key = eng.key if eng else (_keys(provider) or [""])[0]
    pics = [(t, base64.b64encode(b).decode()) for t, b in images or []]
    sys_prompt = system + "\n\nReply with ONLY one valid JSON object. No markdown fences, no commentary."
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            if provider == "anthropic":
                r = await client.post("https://api.anthropic.com/v1/messages", headers={
                    "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens + THINKING_HEADROOM, "system": sys_prompt,
                          "messages": [{"role": "user", "content": [*({"type": "image", "source": {"type": "base64", "media_type": t, "data": d}}
                                                                       for t, d in pics), {"type": "text", "text": prompt}] if pics else prompt}],
                          **({"output_config": {"effort": settings.ai_effort}} if settings.ai_effort else {})})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"anthropic {r.status_code}", _retry_after(r))
                if r.status_code != 200:
                    raise _failed("anthropic", r)
                d = r.json()
                if d.get("stop_reason") == "max_tokens":
                    raise AIError("anthropic: the answer was cut off (max_tokens)")
                if d.get("stop_reason") == "refusal":
                    raise AIError("anthropic: the request was declined")
                text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                usage = Usage(d.get("usage", {}).get("input_tokens", 0), d.get("usage", {}).get("output_tokens", 0), model)
            elif provider == "gemini":
                r = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"systemInstruction": {"parts": [{"text": sys_prompt}]}, "contents": [{"role": "user", "parts": [*({"inline_data": {"mime_type": t, "data": d}} for t, d in pics), {"text": prompt}]}],
                          "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"gemini {r.status_code}", _retry_after(r))
                if r.status_code != 200:
                    raise _failed("gemini", r)
                d = r.json()
                text = "".join(p.get("text", "") for p in d.get("candidates", [{}])[0].get("content", {}).get("parts", []))
                meta = d.get("usageMetadata", {})
                usage = Usage(meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0), model)
            elif provider == "openai":
                r = await client.post("https://api.openai.com/v1/chat/completions", headers={"authorization": f"Bearer {key}"},
                    json={"model": model, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": [
                              *({"type": "image_url", "image_url": {"url": f"data:{t};base64,{d}"}} for t, d in pics),
                              {"type": "text", "text": prompt}] if pics else prompt}]})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"openai {r.status_code}", _retry_after(r))
                if r.status_code != 200:
                    raise _failed("openai", r)
                d = r.json()
                text = d["choices"][0]["message"]["content"]
                usage = Usage(d.get("usage", {}).get("prompt_tokens", 0), d.get("usage", {}).get("completion_tokens", 0), model)
            else:
                raise AIError(f"Unknown AI_PROVIDER {provider}")
    except httpx.HTTPError as e:
        raise Down("The AI engine could not be reached") from e
    return _json(text), usage


def _failed(provider: str, r: httpx.Response) -> AIError:
    """A refused call. Down when another engine would do better (the engine's own fault, a bad key, no credit); otherwise the
    request itself is wrong and another engine wouldn't help."""
    e = r.text[:200]
    down = r.status_code >= 500 or r.status_code in (401, 402, 403) or any(w in e.lower() for w in ("credit balance", "billing", "quota"))
    return (Down if down else AIError)(f"{provider} {r.status_code}: {e}")


def explain(error: str) -> str:
    """What an engine failure means for whoever runs the hub, and what to change: the owner only ever sees "the AI is busy"."""
    e = error.lower()
    if "credit balance" in e or "billing" in e or "quota" in e or "insufficient" in e:
        return "The AI account is out of credit. Add credit in the provider's console (Anthropic: console.anthropic.com → Billing)."
    if " 401" in e or " 403" in e or "authentication" in e or "api key" in e or "x-api-key" in e:
        return "The API key is wrong, revoked or missing. Check ANTHROPIC_API_KEY (or the key for your AI_PROVIDER) in Railway → Variables."
    if "effort" in e or "output_config" in e:
        return "This model doesn't take a thinking depth. Set AI_EFFORT to blank in Railway → Variables, or clear AI_MODEL."
    if "model" in e and (" 404" in e or "not_found" in e or " 400" in e):
        return "AI_MODEL isn't a model this key can use. Clear AI_MODEL in Railway → Variables to use the built-in default."
    if (" 429" in e or " 529" in e or " 503" in e or "overloaded" in e):
        return "The provider is turning calls away (rate limit or overload). Wait a minute; if it keeps happening, raise the account's rate limits."
    if "could not be reached" in e:
        return "The hub couldn't reach the AI provider (network or timeout). Try again; if it keeps happening, check the provider's status page."
    if "cut off" in e:
        return "The answer ran past its length limit. Try again; if it keeps happening, send this message to the developer."
    if "unreadable" in e:
        return "The AI answered in a form the hub couldn't read. Try again; if it keeps happening, send this message to the developer."
    if "unknown ai_provider" in e:
        return "AI_PROVIDER isn't one of anthropic, gemini, openai or mock. Fix it in Railway → Variables."
    return "Send this message to the developer."


def cost_inr(usage: Usage) -> float:
    return round(usage.input_tokens / 1e6 * settings.ai_cost_in_per_m + usage.output_tokens / 1e6 * settings.ai_cost_out_per_m, 4)
