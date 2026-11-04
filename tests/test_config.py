import pytest

from receipts.config import load_config


def test_key_fallback_and_safe_repr(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("META_API_KEY", "test-secret-key")
    monkeypatch.setenv("MOCKSITE_HOST", "127.0.0.1")
    config = load_config(tmp_path / "absent")
    assert config.api_key == "test-secret-key"
    assert "test-secret-key" not in repr(config)


def test_reject_nonlocal_host(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCKSITE_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="local only"):
        load_config(tmp_path / "absent")
