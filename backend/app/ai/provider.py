"""
One interface, four engines (AI_PROVIDER = anthropic | gemini | openai | mock).
Every call returns (result, usage) so each run's token cost can be recorded and shown on Pulse.
`mock` returns deterministic, profile-based drafts so the whole hub works without a key.
"""
import asyncio
import json
import random
import re
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


# A room of 200 owners pressing AI buttons at the same moment: at most AI_CONCURRENCY calls go out at once from each server
# process, the rest wait their turn, and a "slow down" from the engine is retried (AI_RETRIES times) after the wait it asks for.
_gate: dict = {"sem": None, "size": 0, "loop": None}
BUSY_STATUS = (429, 503, 529)


def _semaphore() -> asyncio.Semaphore:
    size, loop = max(1, settings.ai_concurrency), asyncio.get_running_loop()
    if _gate["sem"] is None or _gate["size"] != size or _gate["loop"] is not loop:
        _gate.update(sem=asyncio.Semaphore(size), size=size, loop=loop)
    return _gate["sem"]


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return min(float(r.headers.get("retry-after", "")), 30.0)
    except ValueError:
        return None


async def complete(system: str, prompt: str, mock, max_tokens: int = 1200) -> tuple[dict, Usage]:
    provider = settings.ai_provider
    if provider == "mock":
        return mock(), Usage()
    attempt = 0
    while True:
        try:
            async with _semaphore():
                return await _call_once(provider, system, prompt, mock, max_tokens)
        except Busy as e:
            if attempt >= settings.ai_retries:
                raise
            attempt += 1
            await asyncio.sleep(e.wait if e.wait is not None else min(2 ** attempt, 8) + random.random())


async def _call_once(provider: str, system: str, prompt: str, mock, max_tokens: int) -> tuple[dict, Usage]:
    """One request to the engine. (`mock` is unused here; the load test swaps this function for a simulated engine.)"""
    model = settings.ai_model or DEFAULT_MODEL.get(provider, "")
    sys_prompt = system + "\n\nReply with ONLY one valid JSON object. No markdown fences, no commentary."
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            if provider == "anthropic":
                r = await client.post("https://api.anthropic.com/v1/messages", headers={
                    "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens + THINKING_HEADROOM, "system": sys_prompt,
                          "messages": [{"role": "user", "content": prompt}],
                          **({"output_config": {"effort": settings.ai_effort}} if settings.ai_effort else {})})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"anthropic {r.status_code}", _retry_after(r))
                d = r.json()
                if r.status_code != 200:
                    raise AIError(f"anthropic {r.status_code}: {r.text[:200]}")
                if d.get("stop_reason") == "max_tokens":
                    raise AIError("anthropic: the answer was cut off (max_tokens)")
                if d.get("stop_reason") == "refusal":
                    raise AIError("anthropic: the request was declined")
                text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
                usage = Usage(d.get("usage", {}).get("input_tokens", 0), d.get("usage", {}).get("output_tokens", 0), model)
            elif provider == "gemini":
                r = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json={"systemInstruction": {"parts": [{"text": sys_prompt}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"maxOutputTokens": max_tokens, "responseMimeType": "application/json"}})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"gemini {r.status_code}", _retry_after(r))
                d = r.json()
                if r.status_code != 200:
                    raise AIError(f"gemini {r.status_code}: {r.text[:200]}")
                text = "".join(p.get("text", "") for p in d.get("candidates", [{}])[0].get("content", {}).get("parts", []))
                meta = d.get("usageMetadata", {})
                usage = Usage(meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0), model)
            elif provider == "openai":
                r = await client.post("https://api.openai.com/v1/chat/completions", headers={"authorization": f"Bearer {settings.openai_api_key}"},
                    json={"model": model, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": prompt}]})
                if r.status_code in BUSY_STATUS:
                    raise Busy(f"openai {r.status_code}", _retry_after(r))
                d = r.json()
                if r.status_code != 200:
                    raise AIError(f"openai {r.status_code}: {r.text[:200]}")
                text = d["choices"][0]["message"]["content"]
                usage = Usage(d.get("usage", {}).get("prompt_tokens", 0), d.get("usage", {}).get("completion_tokens", 0), model)
            else:
                raise AIError(f"Unknown AI_PROVIDER {provider}")
    except httpx.HTTPError as e:
        raise AIError("The AI engine could not be reached") from e
    return _json(text), usage


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
