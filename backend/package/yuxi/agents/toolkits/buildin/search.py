"""统一网页搜索 backend, 支持多后端 fallback + 多 Tavily key 池轮询/熔断。

- 后端: ``duckduckgo`` (零成本, 走 ddgs 库) / ``tavily`` (走 langchain_tavily)。
- 配置: ``YUXI_SEARCH_BACKEND=duckduckgo,tavily`` (逗号分隔, 按顺序 fallback)。
  单值 ``YUXI_SEARCH_BACKEND=auto|duckduckgo|tavily`` 仍兼容。
- Tavily 多 key: ``TAVILY_API_KEYS=key1,key2,key3`` (轮询, key-level 失败自动熔断);
  兼容旧的 ``TAVILY_API_KEY=key1``。
- 失败信号: Tavily 4xx (401/403/429) 视为 key 级别失败, 熔断该 key;
  5xx/timeout/connection error 视为 backend 级别失败, 整个 Tavily 跳过, fallback 到下一个;
  DDG 任何异常都触发 backend fallback (因为是免费兜底)。
- 业务级 "空 results" 视为成功, 不触发 fallback。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import httpx
from langchain_core.tools import BaseTool, tool

from yuxi.utils import logger

# === 通用常量 ===
_DUCKDUCKGO_MAX_RESULTS = 5
_DUCKDUCKGO_REGION = "wt-wt"
_DUCKDUCKGO_BACKEND = "auto"
_KEY_COOLDOWN_DEFAULT = 60
_BACKEND_TIMEOUT_DEFAULT = 15
_SEARCH_SLUG = "web_search"
_DOUBAO_SEARCH_URL = "https://open.feedcoopapi.com/search_api/web_search"

# Tavily 抛的 ValueError 格式: "Error {status_code}: {error_message}"
_TAVILY_ERROR_RE = re.compile(r"^Error\s+(\d{3})\s*:")

# 视为 key-level 失败的 status code (key 死了, 熔断)
_TAVILY_KEY_FAIL_STATUS = frozenset({401, 403, 429})
# 视为 backend-level 失败的 status code (整条 Tavily 跳过, fallback)
_TAVILY_BACKEND_FAIL_STATUS = frozenset({408, 500, 502, 503, 504})


# =============================================================================
# Key Pool
# =============================================================================


class _TavilyKeyPool:
    """线程安全的 Tavily key 轮询池 + 熔断冷却。

    - round-robin 选取可用 key (所有 key 都在冷却中时返回 None)
    - 失败的 key 进入 cooldown, cooldown 期内跳过, 之后自动恢复
    - 冷却时长由 ``cooldown_seconds`` 控制
    """

    def __init__(self, keys: list[str], cooldown_seconds: int = _KEY_COOLDOWN_DEFAULT) -> None:
        if not keys:
            raise ValueError("_TavilyKeyPool requires at least one key")
        self._keys = list(dict.fromkeys(keys))  # 去重保序
        self._cooldown = max(1, cooldown_seconds)
        self._cooldown_until: dict[str, float] = {}
        self._idx = 0
        self._lock = threading.Lock()

    def pick(self) -> str | None:
        """按 round-robin 选一个当前不在冷却中的 key, 全冷却则返回 None。"""
        with self._lock:
            now = time.monotonic()
            available = [k for k in self._keys if self._cooldown_until.get(k, 0.0) <= now]
            if not available:
                return None
            key = available[self._idx % len(available)]
            self._idx += 1
            return key

    def mark_failed(self, key: str, reason: str) -> None:
        """把 key 标记为冷却, cooldown_seconds 内不再被选。"""
        with self._lock:
            self._cooldown_until[key] = time.monotonic() + self._cooldown
            logger.warning(
                "Tavily key cooldown: prefix=%s... cooldown=%ss reason=%s",
                key[:8],
                self._cooldown,
                reason,
            )

    def mark_success(self, key: str) -> None:
        """成功调用后清除 key 的冷却标记 (让它能立刻继续被选)。"""
        with self._lock:
            self._cooldown_until.pop(key, None)

    def stats(self) -> dict[str, int]:
        """当前可用 key 数量 (供诊断/日志)。"""
        with self._lock:
            now = time.monotonic()
            return {
                "total": len(self._keys),
                "available": sum(1 for k in self._keys if self._cooldown_until.get(k, 0.0) <= now),
                "cooldown_seconds": self._cooldown,
            }


# =============================================================================
# 后端抽象
# =============================================================================


class _SearchBackend(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> str: ...


@dataclass
class _BackendResult:
    """chain 层统一的失败归类。"""

    is_key_level: bool  # True → 仅熔断当前 key; False → 整个 backend 失败, fallback
    error: str


def _classify_exception(exc: BaseException) -> _BackendResult:
    """把异常分类为 key-level / backend-level 失败。

    - key-level (熔断 key):  401/403/429 等明确"key 死了"的错误
    - backend-level (fallback):  5xx/timeout/connection error/4xx(其他)
    """
    import requests

    # requests 异常族: timeout / connection / HTTPError
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in _TAVILY_KEY_FAIL_STATUS:
            return _BackendResult(True, f"http {status}: {exc}")
        return _BackendResult(False, f"http {status or '?'}: {exc}")
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return _BackendResult(False, f"network: {type(exc).__name__}: {exc}")
    if isinstance(exc, requests.RequestException):
        return _BackendResult(False, f"request: {type(exc).__name__}: {exc}")

    # langchain_tavily 抛的 ValueError("Error {code}: {msg}")
    if isinstance(exc, ValueError):
        match = _TAVILY_ERROR_RE.match(str(exc))
        if match:
            status = int(match.group(1))
            if status in _TAVILY_KEY_FAIL_STATUS:
                return _BackendResult(True, f"http {status}: {exc}")
            return _BackendResult(False, f"http {status}: {exc}")

    # 其他异常: 兜底按 backend-level 处理 (跳过该 backend, fallback)
    return _BackendResult(False, f"{type(exc).__name__}: {exc}")


# --- DuckDuckGo backend -----------------------------------------------------


def _format_results_for_llm(query: str, results: list[dict[str, str]]) -> str:
    if not results:
        return json.dumps(
            {"query": query, "results": [], "answer": "未检索到结果。"},
            ensure_ascii=False,
        )
    formatted = [
        {
            "title": item.get("title", ""),
            "url": item.get("href") or item.get("url") or "",
            "snippet": item.get("body") or item.get("snippet") or "",
        }
        for item in results
    ]
    return json.dumps({"query": query, "results": formatted}, ensure_ascii=False)


class _DuckDuckGoBackend:
    """DDG 单实例 backend (无 key, 直接走 ddgs 库)。"""

    name: ClassVar[str] = "duckduckgo"

    def search(self, query: str, max_results: int) -> str:
        from ddgs import DDGS

        cap = max(1, min(max_results, 20))
        with DDGS() as client:
            raw = list(
                client.text(
                    query=query,
                    max_results=cap,
                    region=_DUCKDUCKGO_REGION,
                    backend=_DUCKDUCKGO_BACKEND,
                    safesearch="moderate",
                )
            )
        return _format_results_for_llm(query, raw)


class _DoubaoBackend:
    """豆包联网搜索 backend。"""

    name: ClassVar[str] = "doubao"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def search(self, query: str, max_results: int) -> str:
        payload = {
            "Query": query[:100],
            "SearchType": "web",
            "Count": max(1, min(max_results, 50)),
            "Filter": {"NeedUrl": True},
            "ContentFormats": "text",
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=_BACKEND_TIMEOUT_DEFAULT) as client:
            response = client.post(_DOUBAO_SEARCH_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        error_info = data.get("ResponseMetadata", {}).get("Error")
        if error_info:
            raise RuntimeError(error_info.get("Message") or "doubao search failed")
        result_data = data.get("Result") or {}
        results = [
            {
                "title": item.get("Title") or "",
                "url": item.get("Url") or "",
                "snippet": item.get("Summary") or item.get("Snippet") or item.get("Content") or "",
            }
            for item in result_data.get("WebResults") or []
        ]
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)


# --- Tavily backend ---------------------------------------------------------


class _TavilyBackend:
    """Tavily backend, 内部用 ``_TavilyKeyPool`` 轮询多个 key + 熔断。"""

    name: ClassVar[str] = "tavily"

    def __init__(self, key_pool: _TavilyKeyPool) -> None:
        self._pool = key_pool

    def search(self, query: str, max_results: int) -> str:
        cap = max(1, min(max_results, 20))
        last_key_error: _BackendResult | None = None

        for _ in range(self._pool.stats()["total"]):
            key = self._pool.pick()
            if key is None:
                break
            raw = self._invoke_with_key(query, cap, key)
            classified = self._classify_tavily_response(raw)
            if classified is None:
                # 成功
                self._pool.mark_success(key)
                return _format_tavily_results(query, raw)
            # 失败
            if classified.is_key_level:
                self._pool.mark_failed(key, classified.error)
                last_key_error = classified
                continue
            # backend-level: 让 chain 层 fallback
            raise RuntimeError(f"tavily backend error: {classified.error}")

        # 所有 key 都死了
        if last_key_error is not None:
            raise RuntimeError(f"all tavily keys exhausted: {last_key_error.error}")
        raise RuntimeError("no tavily keys available")

    def _invoke_with_key(self, query: str, cap: int, key: str) -> Any:
        """调一次 Tavily 并返回原始响应。异常转成 RuntimeError 让上游统一处理。"""
        from langchain_tavily import TavilySearch

        try:
            client = TavilySearch(tavily_api_key=key, max_results=cap)
            return client.invoke(query)
        except Exception as exc:  # noqa: BLE001
            # 直接抛 (langchain 工具层 .invoke 会把异常吞进 error 字段, 但底层 _run / raw_results 仍会抛)
            classified = _classify_exception(exc)
            raise RuntimeError(
                f"tavily http {classified.error}" if "http" in classified.error else classified.error
            ) from exc

    def _classify_tavily_response(self, raw: Any) -> _BackendResult | None:
        """检测 Tavily 响应里的 ``error`` 字段 (langchain 工具层会把异常塞这里), 失败时按 status code 分类。

        返回 None 表示成功;返回 _BackendResult 表示失败 (含 key-level / backend-level 分类)。
        """
        if not isinstance(raw, dict):
            return None

        error_field = raw.get("error")
        if error_field is None:
            return None

        # error 可能是 Exception 实例 (langchain tool 协议) 或字符串/字典
        message = str(error_field)

        # 尝试从消息里 parse status code (langchain_tavily 格式: "Error 401: ...")
        match = _TAVILY_ERROR_RE.search(message)
        if match:
            fake_exc = ValueError(message)
            return _classify_exception(fake_exc)
        # 解析不出 status code → 当 backend-level 处理 (网络/未知问题)
        return _BackendResult(False, message[:200])


def _format_tavily_results(query: str, raw: Any) -> str:
    """把 Tavily 原始返回 (dict 或 str) 规整成统一 JSON 格式。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps({"query": query, "results": [], "raw": raw}, ensure_ascii=False)

    if not isinstance(raw, dict):
        return json.dumps({"query": query, "results": [], "raw": raw}, ensure_ascii=False)

    results = raw.get("results") or []
    formatted = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in results
        if isinstance(item, dict)
    ]
    answer = raw.get("answer")
    payload: dict[str, Any] = {"query": query, "results": formatted}
    if answer:
        payload["answer"] = answer
    return json.dumps(payload, ensure_ascii=False)


# =============================================================================
# Chain (按配置顺序 fallback)
# =============================================================================


class _SearchChain:
    """按顺序尝试每个 backend, 全部失败时返回错误 JSON (不抛异常, 让 LLM 看到结果)。"""

    def __init__(self, backends: list[_SearchBackend]) -> None:
        if not backends:
            raise ValueError("_SearchChain requires at least one backend")
        self._backends = backends

    def search(self, query: str, max_results: int) -> str:
        errors: list[str] = []
        for backend in self._backends:
            try:
                result = backend.search(query, max_results)
                if errors:
                    logger.info(
                        "search chain succeeded on backend=%s after %d failure(s): %s",
                        backend.name,
                        len(errors),
                        errors,
                    )
                return result
            except Exception as exc:  # noqa: BLE001
                # 业务级 "空 results" 不会进 except (因为 backend 不抛), 所以这里都是真正的失败
                errors.append(f"{backend.name}: {type(exc).__name__}: {exc}")
                logger.warning("search backend=%s failed: %s", backend.name, exc)
                continue
        return json.dumps(
            {
                "query": query,
                "results": [],
                "error": "all backends failed: " + " | ".join(errors),
            },
            ensure_ascii=False,
        )


# =============================================================================
# 工具构造 + 配置解析
# =============================================================================


def _resolve_backend_list() -> list[str]:
    """解析 ``YUXI_SEARCH_BACKEND``, 兼容单值/逗号分隔/auto。

    auto 模式下: 有 TAVILY key 走 [tavily, duckduckgo], 没有走 [duckduckgo]。
    """
    raw = (os.getenv("YUXI_SEARCH_BACKEND") or os.getenv("WEB_SEARCH_PROVIDER") or "auto").strip()
    has_tavily = bool(_resolve_tavily_keys())
    has_doubao = bool(_resolve_doubao_key())

    if raw == "auto":
        backends = []
        if has_doubao:
            backends.append("doubao")
        if has_tavily:
            backends.append("tavily")
        return [*backends, "duckduckgo"]

    if "," in raw:
        # 去重保序: dict.fromkeys 保留首次出现的位置
        return list(dict.fromkeys(b.strip().lower() for b in raw.split(",") if b.strip()))

    # 兼容单值: doubao / tavily / duckduckgo / 未知 → auto
    single = raw.lower()
    if single in {"doubao", "tavily", "duckduckgo"}:
        return [single]
    logger.warning("Unknown YUXI_SEARCH_BACKEND=%r, fallback to 'auto'", raw)
    backends = []
    if has_doubao:
        backends.append("doubao")
    if has_tavily:
        backends.append("tavily")
    return [*backends, "duckduckgo"]


def _resolve_doubao_key() -> str:
    return os.getenv("DOUBAO_SEARCH_API_KEY", "").strip()


def _resolve_tavily_keys() -> list[str]:
    """解析 Tavily key 列表, 优先 ``TAVILY_API_KEYS``, 兼容 ``TAVILY_API_KEY``。"""
    multi = os.getenv("TAVILY_API_KEYS", "").strip()
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    single = os.getenv("TAVILY_API_KEY", "").strip()
    return [single] if single else []


def _resolve_key_cooldown() -> int:
    raw = os.getenv("YUXI_SEARCH_KEY_COOLDOWN", str(_KEY_COOLDOWN_DEFAULT)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid YUXI_SEARCH_KEY_COOLDOWN=%r, use default %s", raw, _KEY_COOLDOWN_DEFAULT)
        return _KEY_COOLDOWN_DEFAULT


def _build_chain_tool(backends: list[_SearchBackend]) -> BaseTool:
    chain = _SearchChain(backends)

    @tool
    def tavily_search(  # noqa: D401 — 保留 slot 名以兼容既有 skill/tool_dependencies
        query: str,
        max_results: int | None = None,
    ) -> str:
        """执行一次网页搜索并返回结构化结果。

        内部按 ``YUXI_SEARCH_BACKEND`` 配置顺序尝试多个 backend (含多 Tavily key 轮询 + 熔断),
        所有都失败时返回 ``error`` 字段 (不抛异常)。

        Args:
            query: 搜索关键词或问题。
            max_results: 返回结果条数, 默认 5, 上限 20。

        Returns:
            JSON 字符串, 形如 ``{"query":..., "results":[{"title","url","snippet"}]}``。
        """
        cap = max_results if max_results is not None else _DUCKDUCKGO_MAX_RESULTS
        return chain.search(query, cap)

    tavily_search.name = _SEARCH_SLUG
    tavily_search.description = (
        "使用配置的网页搜索 backend 链 (可含 DuckDuckGo + 多个 Tavily key) 检索公开网络信息。"
        "输入: query (必填) + max_results (1-20, 默认 5)。"
        "输出: JSON 字符串, results 列表含 title / url / snippet。"
    )
    return tavily_search


def create_search_tool() -> BaseTool | None:
    """根据 env 构造网页搜索工具 (chain + 多 key 池)。"""
    backend_names = _resolve_backend_list()
    tavily_keys = _resolve_tavily_keys()
    cooldown = _resolve_key_cooldown()

    backends: list[_SearchBackend] = []
    for name in backend_names:
        if name == "duckduckgo":
            backends.append(_DuckDuckGoBackend())
        elif name == "doubao":
            doubao_key = _resolve_doubao_key()
            if not doubao_key:
                logger.info("doubao requested in chain but no DOUBAO_SEARCH_API_KEY set, skipping")
                continue
            backends.append(_DoubaoBackend(doubao_key))
        elif name == "tavily":
            if not tavily_keys:
                logger.info("tavily requested in chain but no TAVILY_API_KEY(S) set, skipping")
                continue
            pool = _TavilyKeyPool(tavily_keys, cooldown_seconds=cooldown)
            backends.append(_TavilyBackend(pool))
        else:
            logger.warning("Unknown search backend=%r in chain, skipping", name)

    if not backends:
        logger.warning("no usable search backends configured (YUXI_SEARCH_BACKEND=%s)", backend_names)
        return None

    return _build_chain_tool(backends)


def resolve_search_backend() -> str:
    """诊断用: 返回当前 chain 的 backend 列表的字符串表示。"""
    return ",".join(_resolve_backend_list())


def search_tool_metadata(_backend: str = "") -> dict[str, Any]:
    """工具展示元数据。chain 模式下根据实际 backend 列表动态生成。"""
    backend_names = _resolve_backend_list()
    tavily_keys = _resolve_tavily_keys()
    pool_stats = {"tavily_keys": len(tavily_keys), "cooldown_seconds": _resolve_key_cooldown()}

    display = "网页搜索"
    guide_lines = [
        f"按顺序尝试: {', '.join(backend_names)};前一个失败自动 fallback 到下一个。",
    ]
    if "tavily" in backend_names:
        if len(tavily_keys) > 1:
            cooldown_text = f"{pool_stats['cooldown_seconds']}s"
            guide_lines.append(
                f"Tavily 共 {len(tavily_keys)} 个 key 轮询使用;key 失败 (401/403/429) 自动熔断 {cooldown_text}。"
            )
        elif len(tavily_keys) == 1:
            guide_lines.append("Tavily 单 key 模式;key 失败 (401/403/429) 熔断后直接 fallback。")
        else:
            guide_lines.append("Tavily 未配置 key,该 backend 会被跳过。")
    if "duckduckgo" in backend_names:
        guide_lines.append("DuckDuckGo 零成本、无需 API key,异常时自动 fallback 到下一条。")
    if "doubao" in backend_names:
        guide_lines.append("豆包搜索使用 DOUBAO_SEARCH_API_KEY。")
    guide_lines.append(
        "配置: YUXI_SEARCH_BACKEND=doubao,tavily,duckduckgo / "
        "TAVILY_API_KEYS=key1,key2 / YUXI_SEARCH_KEY_COOLDOWN=60。"
    )

    return {
        "display_name": display,
        "config_guide": " ".join(guide_lines),
        "chain": backend_names,
    }


__all__ = [
    "create_search_tool",
    "resolve_search_backend",
    "search_tool_metadata",
    "_TavilyKeyPool",  # 仅供测试
    "_BackendResult",  # 仅供测试
    "_classify_exception",  # 仅供测试
    "_resolve_backend_list",  # 仅供测试
    "_resolve_doubao_key",  # 仅供测试
    "_resolve_tavily_keys",  # 仅供测试
]
