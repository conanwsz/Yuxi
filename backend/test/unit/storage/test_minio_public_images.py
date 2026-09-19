import json

import pytest

from yuxi.storage.minio.client import MinIOClient, normalize_public_minio_url


class FakeMinio:
    def __init__(self):
        self.policy = None

    def bucket_exists(self, bucket_name: str) -> bool:
        return False

    def make_bucket(self, bucket_name: str) -> None:
        return None

    def set_bucket_policy(self, bucket_name: str, policy: str) -> None:
        self.policy = json.loads(policy)

    def put_object(self, **kwargs):
        return object()


def test_public_image_uses_same_origin_url_without_bucket_listing(monkeypatch):
    # vuln-0007: 凭证缺失即启动失败 (fail-closed),测试需显式注入占位凭证。
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")
    client = MinIOClient()
    fake_minio = FakeMinio()
    client._client = fake_minio

    result = client.upload_file("public", "images/user 1/avatar.png", b"image", "image/png")

    assert result.url == "/minio/public/images/user%201/avatar.png"
    assert fake_minio.policy is not None
    actions = [action for statement in fake_minio.policy["Statement"] for action in statement["Action"]]
    assert actions == ["s3:GetObject"]


def test_minio_client_init_fails_closed_when_access_key_missing(monkeypatch):
    # vuln-0007: 移除 minioadmin 默认回退后,env 缺失必须抛 KeyError 拒绝启动。
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    with pytest.raises(KeyError, match="MINIO_ACCESS_KEY"):
        MinIOClient()


def test_minio_client_init_fails_closed_when_secret_key_missing(monkeypatch):
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-access-key")
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    with pytest.raises(KeyError, match="MINIO_SECRET_KEY"):
        MinIOClient()


def test_legacy_public_minio_url_is_normalized_to_same_origin(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    assert (
        normalize_public_minio_url("http://example.test:9000/public/avatar/user.png") == "/minio/public/avatar/user.png"
    )
    assert normalize_public_minio_url("https://cdn.example.test/public/user.png") == (
        "https://cdn.example.test/public/user.png"
    )


def test_legacy_public_minio_url_preserves_query_and_fragment(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_URL", "/minio")

    assert (
        normalize_public_minio_url("http://example.test:9000/public/avatar/user.png?v=123#preview")
        == "/minio/public/avatar/user.png?v=123#preview"
    )
