from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tradeagent.config import Config
from tradeagent.llm import LLMAdvisor, _strip_code_fence


@pytest.fixture()
def gemini_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    cfg = Config({"llm": {"enabled": True, "provider": "gemini", "model": "gemini-2.5-flash",
                          "timeout_s": 5, "every_cycles": 1, "max_calls_per_hour": 2}}, root=tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return cfg


def test_disabled_provider(tmp_path: Path) -> None:
    cfg = Config({"llm": {"enabled": False, "provider": "gemini"}}, root=tmp_path)
    advisor = LLMAdvisor(cfg, tmp_path / "calls.json")
    ok, why = advisor.available()
    assert not ok
    result = advisor.consult({"cycle": 1})
    assert result.proposal is None
    assert "غیرفعال" in result.meta["error"]


def test_gemini_parses_json(gemini_cfg: Config, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        calls.append({"url": url, "payload": payload})
        return {
            "candidates": [{"content": {"parts": [{"text": '{"variant":"meanrev","params":{"z_entry":1.9},"reason":"ok","confidence":0.8}'}]}}],
            "usageMetadata": {"totalTokenCount": 42},
        }

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    ok, _ = advisor.available()
    assert ok
    result = advisor.consult({"cycle": 3})
    assert result.proposal is not None
    assert result.proposal["variant"] == "meanrev"
    assert result.meta["ok"] is True
    assert result.meta["usage"]["totalTokenCount"] == 42
    assert "key=test-key" in calls[0]["url"]
    assert advisor.calls_last_hour() == 1


def test_bad_json_returns_none(gemini_cfg: Config, tmp_path: Path) -> None:
    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": "sorry, no json"}]}}]}

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    result = advisor.consult({"cycle": 1})
    assert result.proposal is None
    assert "JSON" in (result.meta.get("error") or "")


def test_network_error_is_swallowed(gemini_cfg: Config, tmp_path: Path) -> None:
    import urllib.error

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        raise urllib.error.URLError("no network")

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    result = advisor.consult({"cycle": 1})
    assert result.proposal is None
    assert "URLError" in result.meta["error"]


def test_quota_limits_calls(gemini_cfg: Config, tmp_path: Path) -> None:
    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": '{"variant":"momentum","params":{"rr":1.7},"confidence":0.9}'}]}}]}

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    assert advisor.consult({}).proposal is not None
    assert advisor.consult({}).proposal is not None
    third = advisor.consult({})
    assert third.proposal is None
    assert "سهمیه" in third.meta["error"]


def test_ollama_provider(tmp_path: Path) -> None:
    cfg = Config({"llm": {"enabled": True, "provider": "ollama", "ollama_url": "http://127.0.0.1:11434",
                          "ollama_model": "qwen2.5:7b-instruct", "max_calls_per_hour": 5}}, root=tmp_path)

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        assert url.endswith("/api/chat")
        assert payload["format"] == "json"
        return {"message": {"content": '{"variant":"scalp","params":{"hold_s":200},"confidence":0.6}'}}

    advisor = LLMAdvisor(cfg, tmp_path / "calls.json", transport=transport)
    result = advisor.consult({})
    assert result.proposal == {"variant": "scalp", "params": {"hold_s": 200}, "confidence": 0.6}
    assert result.meta["model"] == "qwen2.5:7b-instruct"


def test_should_consult_respects_interval(gemini_cfg: Config, tmp_path: Path) -> None:
    gemini_cfg.set("llm.every_cycles", 3)
    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json")
    assert advisor.should_consult(1) is False
    assert advisor.should_consult(3) is True
    assert advisor.should_consult(2, force=True) is True


def test_strip_code_fence() -> None:
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('prefix {"a":1} suffix') == '{"a":1}'
    assert _strip_code_fence("no braces") == "no braces"


def test_gemini_switches_to_the_model_google_suggests(gemini_cfg: Config, tmp_path: Path) -> None:
    """مدل بازنشسته‌شده: گوگل ۴۰۴ می‌دهد و جانشین را نام می‌برد → همان را بردار."""
    import io
    import urllib.error

    urls: list[str] = []

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        urls.append(url)
        if "gemini-2.5-flash" in url:
            body = (b'{"error":{"code":404,"message":"This model models/gemini-2.5-flash is no '
                    b'longer available to new users. Please update your code to use models/gemini-3.8-flash"}}')
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(body))
        return {"candidates": [{"content": {"parts": [{"text": '{"variant":"momentum","params":{},"reason":"ok","confidence":0.6}'}]}}]}

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    result = advisor.consult({"cycle": 5})
    assert result.proposal is not None, result.meta
    assert result.meta.get("model_switched_to") == "gemini-3.8-flash"
    assert advisor.model == "gemini-3.8-flash"
    assert "gemini-3.8-flash" in urls[-1]


def test_suggested_model_ignores_unrelated_errors() -> None:
    from tradeagent.llm import suggested_model

    assert suggested_model("API key not valid. Please pass a valid API key.") is None
    assert suggested_model("") is None
    assert suggested_model(
        "This model models/gemini-2.5-flash is no longer available to new users. "
        "Please update your code to use models/gemini-3.8-flash"
    ) == "gemini-3.8-flash"


def test_transient_503_is_retried_on_the_same_model(gemini_cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error

    from tradeagent import llm as llm_mod

    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    seen: list[str] = []

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
        seen.append(url)
        if len(seen) == 1:
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b'{"error":{"code":503}}'))
        return {"candidates": [{"content": {"parts": [{"text": '{"variant":"scalp","params":{},"reason":"ok","confidence":0.5}'}]}}]}

    advisor = LLMAdvisor(gemini_cfg, tmp_path / "calls.json", transport=transport)
    result = advisor.consult({"cycle": 2})
    assert result.proposal is not None, result.meta
    assert "gemini-2.5-flash" in seen[0] and "gemini-2.5-flash" in seen[1]
    assert result.meta.get("model_switched_to") is None


def test_speech_models_are_not_used() -> None:
    from tradeagent.llm import is_text_model, suggested_model

    assert is_text_model("gemini-3.8-flash")
    assert not is_text_model("gemini-3.8-flash-tts")
    assert not is_text_model("gemini-3.8-flash-image")
    # پیشنهاد گوگل اگر مدل گفتاری باشد نادیده گرفته می‌شود
    assert suggested_model("use models/gemini-3.8-flash-tts") is None
