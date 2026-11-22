import json
import logging
from hashlib import sha256
from time import perf_counter

import litellm
from litellm.caching import Cache

logger = logging.getLogger(__name__)


class ProvidersUnavailable(Exception):
    pass


class Gateway:
    def __init__(self, model: str, keys: tuple[str, ...], timeout: float, api_base: str | None = None):
        self.model = model
        self.keys = keys
        self.timeout = timeout
        self.api_base = api_base
        self.cache = Cache(type="local", ttl=3600)

    async def reply(self, messages, simulation, started):
        # Canonical JSON preserves roles, message boundaries, and exact whitespace.
        print("\n--- MESSAGES SENT TO CACHE ---")
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        # payload = json.dumps([self.model, messages], ensure_ascii=False, sort_keys=True)
        # cache_key = sha256(payload.encode()).hexdigest()
        system_prompt = messages[0]["content"]
        latest_user_message = messages[-1]["content"]

        payload = json.dumps(
            [self.model, system_prompt, latest_user_message],
            ensure_ascii=False,
            sort_keys=True,
        )

        cache_key = sha256(payload.encode()).hexdigest()
        cached = self.cache.get_cache(cache_key=cache_key) if not simulation else None
        print("CACHE KEY:", cache_key)
        print("CACHE HIT:", cached is not None)
        if cached is not None:
            return cached["reply"], self.metadata(cached, started, None, 0)
        

        for index, key in enumerate(self.keys):
            key_id = f"key_{index + 1}"
            status = simulation.get(key_id, simulation.get("*"))
            try:
                if status:
                    error = litellm.RateLimitError if status == 429 else litellm.InternalServerError
                    raise error(message=f"Simulated HTTP {status}", model=self.model, llm_provider="simulation")
                result = await litellm.acompletion(
                    model=self.model,
                    api_key=key,
                    api_base=self.api_base,
                    messages=messages,
                    timeout=self.timeout,
                    num_retries=0,
                    caching=False,
                )
                reply = result.choices[0].message.content
                usage = result.usage
                if not reply or not reply.strip() or usage is None:
                    raise ValueError("Completion is missing text or usage")
                data = {
                    "reply": reply,
                    "input_tokens": int(usage.prompt_tokens),
                    "output_tokens": int(usage.completion_tokens),
                }
            except Exception as error:
                # Provider exceptions can include sensitive request data.
                status_code = getattr(error, "status_code", None)
                logger.warning(
                    "Completion failed for %s (%s, HTTP %s)",
                    key_id,
                    type(error).__name__,
                    status_code if isinstance(status_code, int) else "unknown",
                )
                continue
            if not simulation:
                self.cache.add_cache(data, cache_key=cache_key)
            return reply, self.metadata(data, started, key_id, index)

        raise ProvidersUnavailable

    def metadata(self, data, started, key_id, attempts):
        return {
            "resolution_source": "LIVE_INFERENCE" if key_id else "LLM_CACHE",
            "model": self.model,
            "latency_ms": round((perf_counter() - started) * 1000),
            "input_tokens": data["input_tokens"],
            "output_tokens": data["output_tokens"],
            "key_id": key_id,
            "fallback_attempts": attempts,
        }
