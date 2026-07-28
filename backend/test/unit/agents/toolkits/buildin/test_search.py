"""测试 buildin/search.py: chain fallback + Tavily key 池轮询/熔断。"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
import requests

from yuxi.agents.toolkits.buildin import search


# =============================================================================
# 配置解析
# =============================================================================


def test_resolve_backend_list_auto_without_keys_is_duckduckgo_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YUXI_SEARCH_BACKEND", raising=False)
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert search._resolve_backend_list() == ["duckduckgo"]


def test_resolve_backend_list_auto_with_keys_prefers_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YUXI_SEARCH_BACKEND", raising=False)
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-a,tvly-b")
    assert search._resolve_backend_list() == ["tavily", "duckduckgo"]


def test_resolve_backend_list_supports_comma_separated_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    assert search._resolve_backend_list() == ["duckduckgo", "tavily"]
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "tavily,duckduckgo,tavily")
    # 去重保序
    assert search._resolve_backend_list() == ["tavily", "duckduckgo"]


def test_resolve_backend_list_unknown_value_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "totally-bogus")
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    assert search._resolve_backend_list() == ["duckduckgo"]


def test_resolve_tavily_keys_prefers_keys_then_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEYS", "k1, k2 ,k3")
    assert search._resolve_tavily_keys() == ["k1", "k2", "k3"]
    monkeypatch.delenv("TAVILY_API_KEYS")
    monkeypatch.setenv("TAVILY_API_KEY", "single")
    assert search._resolve_tavily_keys() == ["single"]


def test_resolve_key_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YUXI_SEARCH_KEY_COOLDOWN", raising=False)
    assert search._resolve_key_cooldown() == 60
    monkeypatch.setenv("YUXI_SEARCH_KEY_COOLDOWN", "5")
    assert search._resolve_key_cooldown() == 5
    monkeypatch.setenv("YUXI_SEARCH_KEY_COOLDOWN", "bogus")
    assert search._resolve_key_cooldown() == 60  # 兜底


# =============================================================================
# 异常分类
# =============================================================================


def _http_error(status: int, msg: str = "") -> requests.HTTPError:
    """构造带 response 的 HTTPError (用 positional 第一个参数当 message)。"""
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(msg or f"http {status}", response=response)


@pytest.mark.parametrize(
    "status,expected_key_level",
    [
        (401, True),
        (403, True),
        (429, True),
        (408, False),  # timeout
        (500, False),
        (502, False),
        (503, False),
        (504, False),
        (400, False),  # 4xx 其他算 backend-level
    ],
)
def test_classify_http_error(status: int, expected_key_level: bool) -> None:
    result = search._classify_exception(_http_error(status))
    assert result.is_key_level is expected_key_level, f"status={status} got {result}"


def test_classify_timeout_is_backend_level() -> None:
    result = search._classify_exception(requests.Timeout("read timeout"))
    assert result.is_key_level is False
    assert "Timeout" in result.error


def test_classify_connection_error_is_backend_level() -> None:
    result = search._classify_exception(requests.ConnectionError("dns"))
    assert result.is_key_level is False


def test_classify_tavily_value_error_with_429_is_key_level() -> None:
    """langchain_tavily 抛 ValueError(\"Error 429: ...\"), 应该被识别为 key-level。"""
    result = search._classify_exception(ValueError("Error 429: rate limit exceeded"))
    assert result.is_key_level is True
    assert "429" in result.error


def test_classify_tavily_value_error_with_5xx_is_backend_level() -> None:
    result = search._classify_exception(ValueError("Error 503: service unavailable"))
    assert result.is_key_level is False


def test_classify_unknown_exception_is_backend_level_fallback() -> None:
    result = search._classify_exception(RuntimeError("boom"))
    assert result.is_key_level is False


# =============================================================================
# TavilyKeyPool
# =============================================================================


def test_key_pool_picks_round_robin() -> None:
    pool = search._TavilyKeyPool(["k1", "k2", "k3"], cooldown_seconds=60)
    picked = [pool.pick() for _ in range(6)]
    assert picked == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_key_pool_skips_keys_in_cooldown() -> None:
    pool = search._TavilyKeyPool(["k1", "k2", "k3"], cooldown_seconds=60)
    pool.mark_failed("k2", "401")
    # 6 次 pick 不应再出现 k2
    for _ in range(6):
        assert pool.pick() in {"k1", "k3"}


def test_key_pool_returns_none_when_all_keys_in_cooldown() -> None:
    pool = search._TavilyKeyPool(["k1", "k2"], cooldown_seconds=60)
    pool.mark_failed("k1", "401")
    pool.mark_failed("k2", "429")
    assert pool.pick() is None


def test_key_pool_releases_cooldown_after_duration() -> None:
    pool = search._TavilyKeyPool(["k1"], cooldown_seconds=1)
    pool.mark_failed("k1", "401")
    assert pool.pick() is None
    time.sleep(1.1)
    assert pool.pick() == "k1"


def test_key_pool_mark_success_clears_cooldown() -> None:
    pool = search._TavilyKeyPool(["k1", "k2"], cooldown_seconds=60)
    pool.mark_failed("k1", "401")
    pool.mark_success("k1")
    stats = pool.stats()
    assert stats["available"] == 2


def test_key_pool_dedupes_keys() -> None:
    pool = search._TavilyKeyPool(["k1", "k1", "k2", "k2", "k3"])
    assert pool.stats()["total"] == 3


def test_key_pool_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        search._TavilyKeyPool([])


# =============================================================================
# Backend (mock)
# =============================================================================


class _FakeDDGS:
    def __init__(self, results=None, raise_exc: Exception | None = None):
        self._results = results or []
        self._raise = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, **_kwargs):
        if self._raise is not None:
            raise self._raise
        return iter(self._results)


def _patch_ddgs(results=None, raise_exc: Exception | None = None):
    return patch("ddgs.DDGS", return_value=_FakeDDGS(results, raise_exc), create=True)


# =============================================================================
# create_search_tool: chain fallback 行为
# =============================================================================


def test_chain_falls_back_to_tavily_when_ddg_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-1")
    monkeypatch.setenv("YUXI_SEARCH_KEY_COOLDOWN", "60")

    with _patch_ddgs(raise_exc=RuntimeError("ddg down")):
        # mock TavilySearch 只在它被实际调用时才用, 这里先看 chain 走到 Tavily
        with patch(
            "yuxi.agents.toolkits.buildin.search._TavilyBackend.search",
            return_value=json.dumps(
                {"query": "x", "results": [{"title": "t", "url": "u", "snippet": "s"}]},
                ensure_ascii=False,
            ),
        ) as tavily_search_mock:
            tool = search.create_search_tool()
            assert tool is not None
            raw = tool.invoke({"query": "x"})
            payload = json.loads(raw)
            assert payload["results"][0]["title"] == "t"
            assert tavily_search_mock.called


def test_chain_returns_error_when_all_backends_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DDG 和 Tavily 都炸 → 工具不抛异常, 返回 error JSON。"""
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-1")

    with _patch_ddgs(raise_exc=RuntimeError("ddg broken")):
        with patch(
            "yuxi.agents.toolkits.buildin.search._TavilyBackend.search",
            side_effect=RuntimeError("tavily broken"),
        ):
            tool = search.create_search_tool()
            raw = tool.invoke({"query": "q"})
            payload = json.loads(raw)
            assert payload["results"] == []
            assert "all backends failed" in payload["error"]
            assert "duckduckgo" in payload["error"]
            assert "tavily" in payload["error"]


def test_empty_results_is_not_treated_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """搜不到东西 (空 results) 不应触发 fallback, 应该直接返回。"""
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-1")

    with _patch_ddgs(results=[]):
        with patch("yuxi.agents.toolkits.buildin.search._TavilyBackend.search") as tavily_search_mock:
            tool = search.create_search_tool()
            raw = tool.invoke({"query": "nothing matches"})
            payload = json.loads(raw)
            assert payload["results"] == []
            assert "未检索到结果" in payload["answer"]
            assert not tavily_search_mock.called  # 没走到 Tavily


def test_tavily_backend_iterates_keys_and_marks_cooldown_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tavily 第一个 key 401 (key-level) → 熔断 + 试第二个 key。

    真实生产里 ``TavilySearch.invoke()`` 不抛异常, 而是把错误塞 ``error`` 字段。
    """
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-bad,tvly-good")
    monkeypatch.setenv("YUXI_SEARCH_KEY_COOLDOWN", "60")

    keys_used: list[str] = []

    def fake_tavily_factory(tavily_api_key, **_kwargs):
        keys_used.append(tavily_api_key)

        class _MockClient:
            def invoke(self, _query, **_kw):
                if tavily_api_key == "tvly-bad":
                    # 模拟 langchain_tavily 真实行为: 不抛, 塞 error 字段
                    return {
                        "error": ValueError("Error 401: invalid api key"),
                        "results": [],
                    }
                return {
                    "results": [{"title": "good", "url": "u", "content": "ok"}],
                    "answer": None,
                }

        return _MockClient()

    with patch("langchain_tavily.TavilySearch", side_effect=fake_tavily_factory):
        tool = search.create_search_tool()
        assert tool is not None
        raw = tool.invoke({"query": "x"})
        payload = json.loads(raw)
        assert payload["results"][0]["title"] == "good"
        # 实际尝试了 bad 和 good 两个 key
        assert keys_used == ["tvly-bad", "tvly-good"]


def test_tavily_response_classifier_401_is_key_level() -> None:
    """模拟 langchain_tavily 把异常塞进 error 字段, 分类器要从 message 里识别 status code。"""
    backend = search._TavilyBackend(search._TavilyKeyPool(["k1"]))
    # 模拟 {"error": Exception("Error 401: ...")}
    result = backend._classify_tavily_response(
        {"error": ValueError("Error 401: Unauthorized: missing or invalid API key."), "results": []}
    )
    assert result is not None
    assert result.is_key_level is True
    assert "401" in result.error


def test_tavily_response_classifier_503_is_backend_level() -> None:
    backend = search._TavilyBackend(search._TavilyKeyPool(["k1"]))
    result = backend._classify_tavily_response({"error": "Error 503: service unavailable", "results": []})
    assert result is not None
    assert result.is_key_level is False


def test_tavily_response_classifier_no_error_is_success() -> None:
    backend = search._TavilyBackend(search._TavilyKeyPool(["k1"]))
    assert backend._classify_tavily_response({"results": []}) is None
    assert backend._classify_tavily_response("plain string") is None
    assert backend._classify_tavily_response(None) is None


def test_tavily_backend_raises_backend_level_on_5xx_no_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tavily 5xx (backend-level) → 不熔断 key, 整个 backend 失败让 chain 跳过。"""
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-1")

    with _patch_ddgs(raise_exc=RuntimeError("ddg down")):
        with patch(
            "langchain_tavily.TavilySearch",
            side_effect=ValueError("Error 503: service unavailable"),
        ):
            tool = search.create_search_tool()
            raw = tool.invoke({"query": "x"})
            payload = json.loads(raw)
            assert "all backends failed" in payload["error"]
            # 503 不该让 key 进 cooldown; 由于 backend 跳过了, 这里也走不通, 整个 chain 失败


def test_no_backends_configured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有任何可用 backend (比如同时缺 key 又禁用 DDG) → 工具 None, 不报错。"""
    # 注: 实际场景下用户至少要启一个 backend, 但代码要容错
    # 因为 _resolve_backend_list() 永远至少返回 ['duckduckgo'], 这个 case 测的是 tavily-only 且没 key
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "tavily")
    monkeypatch.delenv("TAVILY_API_KEYS", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert search.create_search_tool() is None


# =============================================================================
# create_search_tool: slot 注册
# =============================================================================


def test_search_tool_registers_under_tavily_search_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chain 模式注册后 slot 仍然叫 tavily_search (向后兼容)。"""
    from yuxi.agents.toolkits.buildin import tools
    from yuxi.agents.toolkits.registry import _extra_registry

    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-1,tvly-2")

    with _patch_ddgs(results=[]):
        tools._register_search_tool()

    assert "tavily_search" in _extra_registry
    meta = _extra_registry["tavily_search"]
    assert "→" in meta.display_name  # "duckduckgo → tavily" 风格
    assert "TAVILY_API_KEYS" in meta.config_guide
    assert "熔断" in meta.config_guide


def test_metadata_includes_chain_and_pool_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YUXI_SEARCH_BACKEND", "duckduckgo,tavily")
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-a,tvly-b,tvly-c")
    monkeypatch.setenv("YUXI_SEARCH_KEY_COOLDOWN", "120")

    meta = search.search_tool_metadata()
    assert meta["chain"] == ["duckduckgo", "tavily"]
    assert "duckduckgo" in meta["display_name"]
    assert "tavily" in meta["display_name"]
    assert "3 个 key" in meta["config_guide"]
    assert "120s" in meta["config_guide"]
