"""登录滑动验证码的签发与校验。"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass


LOGIN_CAPTCHA_TTL_SECONDS = 300
LOGIN_CAPTCHA_OFFSET_TOLERANCE = 6
LOGIN_CAPTCHA_MIN_OFFSET = 28
LOGIN_CAPTCHA_MAX_OFFSET = 212
LOGIN_CAPTCHA_MIN_VERTICAL_OFFSET = 0
LOGIN_CAPTCHA_MAX_VERTICAL_OFFSET = 36
_signing_secret = os.getenv("JWT_SECRET_KEY") or secrets.token_urlsafe(32)
_CAPTCHA_PALETTES = (
    ("#e0f2fe", "#bfdbfe", "#2563eb", "#1d4ed8"),
    ("#dcfce7", "#bbf7d0", "#15803d", "#166534"),
    ("#fef3c7", "#fde68a", "#d97706", "#b45309"),
    ("#f3e8ff", "#e9d5ff", "#7e22ce", "#6b21a8"),
)
_CAPTCHA_SHAPES = {
    "circle": '<circle cx="18" cy="18" r="15"/>',
    "diamond": '<polygon points="18,2 34,18 18,34 2,18"/>',
    "hexagon": '<polygon points="10,3 26,3 35,18 26,33 10,33 1,18"/>',
    "puzzle": '<path d="M4 4h10a4 4 0 1 0 8 0h10v10a4 4 0 1 1 0 8v10H22a4 4 0 1 0-8 0H4V22a4 4 0 1 1 0-8z"/>',
    "triangle": '<polygon points="18,2 35,33 1,33"/>',
}


@dataclass(frozen=True)
class LoginCaptchaChallenge:
    """供登录页面展示和提交的滑动验证挑战。"""

    token: str
    image_url: str
    piece_image_url: str
    piece_start_y: int


def create_login_captcha(user_id: int) -> LoginCaptchaChallenge:
    """为指定用户签发五分钟内有效的滑动验证码挑战。"""

    payload = {
        "expires_at": int(time.time()) + LOGIN_CAPTCHA_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(12),
        "user_id": user_id,
    }
    target_offset = _derive_target_offset(payload["user_id"], payload["nonce"])
    piece_start_y = _derive_piece_start_y(payload["user_id"], payload["nonce"])
    target_shape = _derive_target_shape(payload["user_id"], payload["nonce"])
    encoded_payload = _encode_payload(payload)
    signature = hmac.new(_signing_secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    token = f"{encoded_payload}.{signature}"
    return LoginCaptchaChallenge(
        token=token,
        image_url=_create_captcha_image(target_offset, piece_start_y, target_shape),
        piece_image_url=_create_captcha_piece_image(target_shape),
        piece_start_y=piece_start_y,
    )


def verify_login_captcha(
    token: str | None,
    user_id: int,
    offset: int | None,
) -> bool:
    """校验挑战签名、有效期、用户绑定与横向拖动位置。"""

    if not token or offset is None:
        return False

    try:
        encoded_payload, signature = token.rsplit(".", 1)
        expected_signature = hmac.new(_signing_secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return False

        payload = json.loads(_decode_payload(encoded_payload))
        return (
            payload["user_id"] == user_id
            and payload["expires_at"] >= int(time.time())
            and abs(_derive_target_offset(payload["user_id"], payload["nonce"]) - offset)
            <= LOGIN_CAPTCHA_OFFSET_TOLERANCE
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _encode_payload(payload: dict[str, int | str]) -> str:
    """使用 URL 安全的 base64 编码挑战内容。"""

    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw_payload).decode().rstrip("=")


def _decode_payload(payload: str) -> str:
    """解码无填充的 URL 安全 base64 内容。"""

    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(f"{payload}{padding}").decode()


def _derive_target_offset(user_id: int, nonce: str) -> int:
    """由签名密钥和随机值推导不出现在令牌中的缺口位置。"""

    digest = hmac.new(_signing_secret.encode(), f"{user_id}:{nonce}".encode(), hashlib.sha256).digest()
    offset_range = LOGIN_CAPTCHA_MAX_OFFSET - LOGIN_CAPTCHA_MIN_OFFSET + 1
    return LOGIN_CAPTCHA_MIN_OFFSET + int.from_bytes(digest[:4], "big") % offset_range


def _derive_piece_start_y(user_id: int, nonce: str) -> int:
    """由挑战随机值稳定推导可拖动拼图的初始高度。"""

    return _derive_bounded_value(
        "piece-y",
        user_id,
        nonce,
        LOGIN_CAPTCHA_MIN_VERTICAL_OFFSET,
        LOGIN_CAPTCHA_MAX_VERTICAL_OFFSET,
    )


def _derive_bounded_value(label: str, user_id: int, nonce: str, minimum: int, maximum: int) -> int:
    """使用签名密钥在给定整数范围内推导稳定随机值。"""

    digest = hmac.new(_signing_secret.encode(), f"{label}:{user_id}:{nonce}".encode(), hashlib.sha256).digest()
    return minimum + int.from_bytes(digest[:4], "big") % (maximum - minimum + 1)


def _derive_target_shape(user_id: int, nonce: str) -> str:
    """由挑战随机值稳定推导本次需要拖动的拼图轮廓。"""

    digest = hmac.new(_signing_secret.encode(), f"shape:{user_id}:{nonce}".encode(), hashlib.sha256).digest()
    return tuple(_CAPTCHA_SHAPES)[int.from_bytes(digest[:4], "big") % len(_CAPTCHA_SHAPES)]


def _create_captcha_image(target_offset: int, target_offset_y: int, target_shape: str) -> str:
    """生成带随机背景、唯一同形干扰项和目标缺口的内嵌 SVG。"""

    start_color, end_color, decoration_color, target_color = secrets.choice(_CAPTCHA_PALETTES)
    decorations = "".join(_create_captcha_decoration(decoration_color) for _ in range(8))
    decoy = _create_captcha_decoy(target_color, target_offset, target_offset_y, target_shape)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="280" height="72" viewBox="0 0 280 72">'
        '<defs><linearGradient id="background" x1="0" x2="1">'
        f'<stop stop-color="{start_color}"/><stop offset="1" stop-color="{end_color}"/>'
        "</linearGradient></defs>"
        '<rect width="280" height="72" rx="8" fill="url(#background)"/>'
        f"{decorations}"
        f"{decoy}"
        f'<g data-target="true" transform="translate({target_offset} {target_offset_y})" fill="{target_color}" '
        f'fill-opacity=".32" stroke="{target_color}" stroke-width="2">'
        f"{_CAPTCHA_SHAPES[target_shape]}</g>"
        "</svg>"
    )
    encoded_svg = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{encoded_svg}"


def _create_captcha_piece_image(shape: str) -> str:
    """创建与目标缺口对应的可拖动拼图图片。"""

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">'
        '<g fill="#ffffff" fill-opacity=".82" stroke="#1d4ed8" stroke-width="2">'
        f"{_CAPTCHA_SHAPES[shape]}</g></svg>"
    )
    return f"data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}"


def _create_captcha_decoy(color: str, target_offset: int, target_offset_y: int, shape: str) -> str:
    """生成一个缩放或旋转不同的同形错误缺口。"""

    x = secrets.randbelow(244)
    y = secrets.randbelow(37)
    if abs(x - target_offset) < 44 and abs(y - target_offset_y) < 30:
        x = (x + 66) % 244

    scale = secrets.choice((0.68, 0.76, 1.24, 1.32))
    rotation = secrets.choice((-28, -18, 18, 28))
    return (
        f'<g data-decoy="true" transform="translate({x} {y})" fill="{color}" fill-opacity=".28" '
        f'stroke="{color}" stroke-width="2"><g transform="rotate({rotation} 18 18)">'
        f'<g transform="translate(18 18) scale({scale}) translate(-18 -18)">{_CAPTCHA_SHAPES[shape]}</g>'
        "</g></g>"
    )


def _create_captcha_decoration(color: str) -> str:
    """创建不构成候选缺口的随机背景线条。"""

    start_y = secrets.randbelow(72)
    control_y = secrets.randbelow(72)
    end_y = secrets.randbelow(72)
    opacity = 20 + secrets.randbelow(20)
    return (
        f'<path d="M0 {start_y} Q140 {control_y} 280 {end_y}" fill="none" stroke="{color}" '
        f'stroke-width="{1 + secrets.randbelow(3)}" stroke-opacity=".{opacity}"/>'
    )
