"""登录滑动验证码服务的单元测试。"""

from base64 import b64decode
import re

from yuxi.services import login_captcha_service


def _target_offset(image_url: str) -> tuple[int, int]:
    """读取服务端 SVG 中展示给用户的缺口位置。"""

    svg = b64decode(image_url.removeprefix("data:image/svg+xml;base64,")).decode()
    match = re.search(r'<g data-target="true" transform="translate\((\d+) (\d+)\)"', svg)
    assert match, svg
    return int(match.group(1)), int(match.group(2))


def test_login_captcha_accepts_the_bound_user_and_target_offset():
    challenge = login_captcha_service.create_login_captcha(user_id=7)
    offset_x, target_y = _target_offset(challenge.image_url)

    assert target_y == challenge.piece_start_y
    assert login_captcha_service.verify_login_captcha(challenge.token, 7, offset_x)
    assert not login_captcha_service.verify_login_captcha(challenge.token, 8, offset_x)


def test_login_captcha_rejects_expired_or_misaligned_challenges(monkeypatch):
    challenge = login_captcha_service.create_login_captcha(user_id=7)
    offset_x, _ = _target_offset(challenge.image_url)

    assert not login_captcha_service.verify_login_captcha(challenge.token, 7, offset_x + 7)
    monkeypatch.setattr(login_captcha_service.time, "time", lambda: 4_102_444_800)
    assert not login_captcha_service.verify_login_captcha(challenge.token, 7, offset_x)


def test_login_captcha_uses_a_new_background_for_each_challenge():
    first = login_captcha_service.create_login_captcha(user_id=7)
    second = login_captcha_service.create_login_captcha(user_id=7)

    assert first.image_url != second.image_url
    assert 0 <= first.piece_start_y <= login_captcha_service.LOGIN_CAPTCHA_MAX_VERTICAL_OFFSET

    svg = b64decode(first.image_url.removeprefix("data:image/svg+xml;base64,")).decode()
    assert svg.count('data-decoy="true"') == 1
    assert "stroke-dasharray" not in svg
