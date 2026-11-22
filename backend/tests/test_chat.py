import asyncio
from types import SimpleNamespace

import litellm
import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("LLM_API_KEYS", "first-provider-key,second-provider-key")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("LLM_MODEL", "gemini/test-model")
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(0.035)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Provider reply"))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=2),
        )

    monkeypatch.setattr(litellm, "acompletion", complete)
    return calls


@pytest.fixture
def client(provider):
    with TestClient(create_app()) as connection:
        yield connection


def chat(client, session, message="Hello", failure=None, **extra):
    headers = {"X-Simulate-Failure": failure} if failure is not None else {}
    return client.post("/chat", json={"session_id": session, "message": message, **extra}, headers=headers)


def test_health_and_cache(client, provider):
    assert client.get("/health").json() == {
        "status": "ok", "keys_configured": 2, "cache_enabled": True,
    }
    live = chat(client, "first", " Hello ").json()
    cached = chat(client, "second").json()
    assert live["session_id"] == "first"
    assert cached["session_id"] == "second"
    assert live["reply"] == cached["reply"]
    assert live["metadata"] == {
        "resolution_source": "LIVE_INFERENCE",
        "model": "gemini/test-model",
        "latency_ms": live["metadata"]["latency_ms"],
        "input_tokens": 12,
        "output_tokens": 2,
        "key_id": "key_1",
        "fallback_attempts": 0,
    }
    assert cached["metadata"]["latency_ms"] < live["metadata"]["latency_ms"]
    assert cached["metadata"]["resolution_source"] == "LLM_CACHE"
    assert cached["metadata"]["key_id"] is None
    assert cached["metadata"]["fallback_attempts"] == 0
    assert cached["metadata"]["input_tokens"] == 12
    assert cached["metadata"]["output_tokens"] == 2
    assert len(provider) == 1
    assert chat(client, "third", "Different question").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_history_and_system_prompt_survive_restart(client, provider):
    first = chat(client, "one", "Remember my code", system_prompt="Use short answers")
    with TestClient(create_app()) as restarted:
        assert chat(restarted, "one", "Repeat it").status_code == 200
        assert provider[1]["messages"] == [
            {"role": "system", "content": "Use short answers"},
            {"role": "user", "content": "Remember my code"},
            {"role": "assistant", "content": "Provider reply"},
            {"role": "user", "content": "Repeat it"},
        ]
        history = restarted.get("/sessions/one/messages").json()
        assert history["session_id"] == "one"
        assert history["messages"][:2] == [
            {"role": "user", "content": "Remember my code"},
            {"role": "assistant", "content": "Provider reply", "metadata": first.json()["metadata"]},
        ]
        assert len(history["messages"]) == 4
        assert restarted.get("/sessions/two/messages").json() == {"detail": "session not found"}


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_fallback_bypasses_warmed_cache(client, provider, status):
    assert chat(client, "warm").status_code == 200
    result = chat(client, "fallback", failure=f"key_1={status}")
    assert result.status_code == 200
    meta = result.json()["metadata"]
    assert (meta["resolution_source"], meta["key_id"], meta["fallback_attempts"]) == ("LIVE_INFERENCE", "key_2", 1)
    assert provider[-1]["api_key"] == "second-provider-key"


@pytest.mark.parametrize("failure", ["*=429", "key_1=429,key_2=500"])
def test_failed_turn_is_not_saved(client, provider, failure):
    result = chat(client, "failed", failure=failure)
    assert result.status_code == 503
    assert result.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/failed/messages").status_code == 404
    assert not provider


def test_real_rate_limit_falls_back(client, monkeypatch):
    calls = []

    async def rate_limited(**kwargs):
        calls.append(kwargs["api_key"])
        if len(calls) == 1:
            raise litellm.RateLimitError(message="quota exceeded", model="test", llm_provider="gemini")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Recovered"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr(litellm, "acompletion", rate_limited)
    result = chat(client, "rate-limited")
    assert result.status_code == 200
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert calls == ["first-provider-key", "second-provider-key"]


@pytest.mark.parametrize("payload", [
    {},
    {"session_id": "", "message": "Hello"},
    {"session_id": " ", "message": "Hello"},
    {"session_id": "x" * 129, "message": "Hello"},
    {"session_id": "invalid", "message": "   "},
    {"session_id": "invalid", "message": "x" * 4001},
])
def test_invalid_messages(client, provider, payload):
    result = client.post("/chat", json=payload)
    assert result.status_code == 422
    assert "detail" in result.json()
    assert not provider


def test_malformed_json(client):
    result = client.post("/chat", content="{", headers={"Content-Type": "application/json"})
    assert result.status_code == 422
    assert "detail" in result.json()


def test_disabled_hook_ignores_header(provider, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    with TestClient(create_app()) as client:
        assert chat(client, "normal", failure="*=429").json()["metadata"]["key_id"] == "key_1"


def test_openai_compatible_gateway_uses_configured_base(provider, monkeypatch):
    monkeypatch.setenv("LLM_API_BASE", "https://api.aicredits.in/v1")
    monkeypatch.setenv("LLM_MODEL", "openai/google/gemini-3.5-flash-lite")
    with TestClient(create_app()) as client:
        result = chat(client, "gateway", failure="key_1=429")

    assert result.status_code == 200
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert provider[0]["api_base"] == "https://api.aicredits.in/v1"
    assert provider[0]["model"] == "openai/google/gemini-3.5-flash-lite"

def test_repeated_question_uses_full_history(client, provider):
    assert chat(client, "repeat", "What is my code?").status_code == 200
    repeat = chat(client, "repeat", "What is my code?").json()

    assert repeat["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert len(provider) == 2
    assert provider[-1]["messages"][1:] == [
        {"role": "user", "content": "What is my code?"},
        {"role": "assistant", "content": "Provider reply"},
        {"role": "user", "content": "What is my code?"},
    ]
