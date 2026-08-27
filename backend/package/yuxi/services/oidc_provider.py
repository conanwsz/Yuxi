"""OIDC Provider 适配器契约。

把不同 OIDC Server 的协议边缘差异收敛为统一的回调、HTTP 请求和用户信息结构。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


type OIDCFieldValue = str | Sequence[str] | None
MAX_CALLBACK_FIELD_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class OIDCCallbackData:
    """标准化后的 OIDC 回调参数。"""

    code: str | None = field(default=None, repr=False)
    state: str | None = field(default=None, repr=False)
    error: str | None = None
    error_description: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class OIDCTokenSet:
    """标准化后的 OIDC Token 响应。"""

    access_token: str = field(repr=False)
    id_token: str = field(repr=False)
    token_type: str | None = None
    refresh_token: str | None = field(default=None, repr=False)
    scope: str | None = None
    expires_in: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OIDCProfile:
    """归一化后的 OIDC 用户资料。"""

    subject: str
    username: str
    email: str
    name: str
    avatar: str | None = None
    department_name: str | None = None
    department_description: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OIDCHTTPRequest:
    """由核心统一执行的 OIDC HTTP 请求描述。"""

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    form: Mapping[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """统一 method 表示，减少调用方判断。"""
        object.__setattr__(self, "method", self.method.upper())


class OIDCProviderMetadataLike(Protocol):
    """OIDC Provider 元数据协议。"""

    authorization_endpoint: str | None
    token_endpoint: str | None
    userinfo_endpoint: str | None


class OIDCProviderAdapter(ABC):
    """OIDC Provider 适配器基类。"""

    provider_type = ""

    def __init__(self, config: Any):
        """保存适配器所需的只读配置。"""
        self.config = config

    @abstractmethod
    def parse_callback(
        self,
        query: Mapping[str, OIDCFieldValue] | None,
        form: Mapping[str, OIDCFieldValue] | None,
    ) -> OIDCCallbackData:
        """把 query 或 form 回调参数归一化。"""

    @abstractmethod
    def authorization_params(self, common_params: Mapping[str, str]) -> dict[str, str]:
        """构造授权请求参数。"""

    @abstractmethod
    def token_request(
        self,
        common_form: Mapping[str, str],
        metadata: OIDCProviderMetadataLike,
    ) -> OIDCHTTPRequest:
        """构造换取 Token 的 HTTP 请求。"""

    @abstractmethod
    def parse_token_response(self, payload: Mapping[str, Any]) -> OIDCTokenSet:
        """把 Token 响应 JSON 归一化。"""

    @abstractmethod
    def userinfo_request(self, access_token: str, metadata: OIDCProviderMetadataLike) -> OIDCHTTPRequest:
        """构造 UserInfo HTTP 请求。"""

    @abstractmethod
    def map_profile(
        self,
        userinfo_payload: Mapping[str, Any],
        verified_id_token_claims: Mapping[str, Any],
    ) -> OIDCProfile:
        """把 UserInfo 与已验证 ID Token Claims 归一化。"""


class StandardOIDCProvider(OIDCProviderAdapter):
    """复现当前 Yuxi 标准 OIDC 行为的适配器。"""

    provider_type = "standard"

    def parse_callback(
        self,
        query: Mapping[str, OIDCFieldValue] | None,
        form: Mapping[str, OIDCFieldValue] | None,
    ) -> OIDCCallbackData:
        """从 query 或 form_post 中读取标准字段。"""
        return OIDCCallbackData(
            code=self._read_callback_field("code", query, form),
            state=self._read_callback_field("state", query, form),
            error=self._read_callback_field("error", query, form),
            error_description=self._read_callback_field("error_description", query, form),
        )

    def authorization_params(self, common_params: Mapping[str, str]) -> dict[str, str]:
        """在标准参数上附加当前配置的授权附加项。"""
        params = dict(common_params)
        if getattr(self.config, "force_prompt_login", False):
            params["prompt"] = "login"
        return params

    def token_request(
        self,
        common_form: Mapping[str, str],
        metadata: OIDCProviderMetadataLike,
    ) -> OIDCHTTPRequest:
        """构造标准的 client_secret_post 换 Token 请求。"""
        token_endpoint = metadata.token_endpoint
        if not token_endpoint:
            raise ValueError("OIDC Token 端点不存在")
        return OIDCHTTPRequest(
            method="POST",
            url=token_endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            form=dict(common_form),
        )

    def parse_token_response(self, payload: Mapping[str, Any]) -> OIDCTokenSet:
        """解析标准顶层 Token JSON。"""
        access_token = self._require_string(payload, "access_token")
        id_token = self._require_string(payload, "id_token")
        token_type = self._read_string(payload, "token_type")
        refresh_token = self._read_string(payload, "refresh_token")
        scope = self._read_string(payload, "scope")
        expires_in = self._read_int(payload, "expires_in")

        return OIDCTokenSet(
            access_token=access_token,
            id_token=id_token,
            token_type=token_type,
            refresh_token=refresh_token,
            scope=scope,
            expires_in=expires_in,
            raw=dict(payload),
        )

    def userinfo_request(self, access_token: str, metadata: OIDCProviderMetadataLike) -> OIDCHTTPRequest:
        """构造标准 GET Bearer UserInfo 请求。"""
        userinfo_endpoint = metadata.userinfo_endpoint
        if not userinfo_endpoint:
            raise ValueError("OIDC UserInfo 端点不存在")
        return OIDCHTTPRequest(
            method="GET",
            url=userinfo_endpoint,
            headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
        )

    def map_profile(
        self,
        userinfo_payload: Mapping[str, Any],
        verified_id_token_claims: Mapping[str, Any],
    ) -> OIDCProfile:
        """按现有配置语义提取标准用户资料。"""
        subject = self._read_string(userinfo_payload, "sub") or ""

        username = self._claim(userinfo_payload, getattr(self.config, "username_claim", "preferred_username"))
        if not username:
            username = self._read_string(userinfo_payload, "preferred_username") or ""
        if not username:
            email = self._read_string(userinfo_payload, "email") or ""
            username = email.partition("@")[0]
        if not username:
            username = subject[:20]

        email = self._claim(userinfo_payload, getattr(self.config, "email_claim", "email"))
        if not email:
            email = self._read_string(userinfo_payload, "email") or ""

        name = self._claim(userinfo_payload, getattr(self.config, "name_claim", "name"))
        if not name:
            name = self._read_string(userinfo_payload, "name") or ""
        if not name:
            name = username

        avatar = self._normalize_optional_text(userinfo_payload.get("picture"))

        department_name = None
        department_description = None
        if getattr(self.config, "fetch_department_info", False):
            department_name = self._claim(userinfo_payload, getattr(self.config, "department_claim", "department"))
            if not department_name:
                department_name = self._read_string(userinfo_payload, "department")

            department_description = self._read_string(userinfo_payload, "department_description")
            if not department_description:
                department_description = self._read_string(userinfo_payload, "department_desc")

        return OIDCProfile(
            subject=subject,
            username=username,
            email=email,
            name=name,
            avatar=avatar,
            department_name=department_name,
            department_description=department_description,
            raw=dict(userinfo_payload),
        )

    def _claim(self, payload: Mapping[str, Any], claim_name: str) -> str | None:
        """读取与当前实现一致的顶层 Claim。"""
        if not claim_name:
            return None
        return self._normalize_optional_text(payload.get(claim_name))

    def _read_callback_field(
        self,
        field_name: str,
        query: Mapping[str, OIDCFieldValue] | None,
        form: Mapping[str, OIDCFieldValue] | None,
    ) -> str | None:
        """读取 query/form 单值字段，并拒绝冲突来源。"""
        query_value = _read_optional_field(query, field_name)
        form_value = _read_optional_field(form, field_name)
        if query_value is not None and form_value is not None:
            raise ValueError(f"OIDC 回调字段 {field_name} 不能同时出现在 query 和 form")
        return query_value if query_value is not None else form_value

    @staticmethod
    def _read_string(payload: Mapping[str, Any], field_name: str) -> str | None:
        """读取可选字符串字段。"""
        value = payload.get(field_name)
        return value if isinstance(value, str) else None

    @staticmethod
    def _require_string(payload: Mapping[str, Any], field_name: str) -> str:
        """读取必填字符串字段。"""
        value = payload.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"OIDC 响应缺少有效的 {field_name}")
        return value

    @staticmethod
    def _read_int(payload: Mapping[str, Any], field_name: str) -> int | None:
        """读取可选整数数值。"""
        value = payload.get(field_name)
        return value if isinstance(value, int) else None

    @staticmethod
    def _normalize_optional_text(value: Any) -> str | None:
        """把空白字符串归一化为 None。"""
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None


OIDC_PROVIDER_REGISTRY: dict[str, type[OIDCProviderAdapter]] = {
    StandardOIDCProvider.provider_type: StandardOIDCProvider,
}


def normalize_provider_type(provider_type: str | None) -> str:
    """标准化 Provider 类型配置。"""
    normalized = (provider_type or "").strip().lower()
    return normalized or StandardOIDCProvider.provider_type


def get_oidc_provider(provider_type: str | None, config: Any) -> OIDCProviderAdapter:
    """按配置值返回静态注册的 OIDC 适配器。"""
    normalized_type = normalize_provider_type(provider_type)
    provider_cls = OIDC_PROVIDER_REGISTRY.get(normalized_type)
    if provider_cls is None:
        supported_types = ", ".join(sorted(OIDC_PROVIDER_REGISTRY))
        raise ValueError(f"未知的 OIDC_PROVIDER_TYPE: {normalized_type}，支持的类型: {supported_types}")
    return provider_cls(config)


def _read_optional_field(source: Mapping[str, OIDCFieldValue] | None, field_name: str) -> str | None:
    """从 query/form 样式字典里读取单值字段。"""
    if source is None or field_name not in source:
        return None

    value = source[field_name]
    if value is None:
        return None
    if isinstance(value, str):
        normalized_value = value
    elif isinstance(value, Sequence):
        values = list(value)
        if not values:
            return None
        if len(values) > 1:
            raise ValueError(f"OIDC 回调字段 {field_name} 出现多个值")
        only_value = values[0]
        if not isinstance(only_value, str):
            raise ValueError(f"OIDC 回调字段 {field_name} 不是字符串")
        normalized_value = only_value
    elif not isinstance(value, str):
        raise ValueError(f"OIDC 回调字段 {field_name} 不是字符串")

    if len(normalized_value) > MAX_CALLBACK_FIELD_LENGTH:
        raise ValueError(f"OIDC 回调字段 {field_name} 过长")
    return normalized_value
