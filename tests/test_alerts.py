"""alert() is best-effort and must never raise into a cycle (#17)."""

from solvent.ops import alerts


def test_alert_disabled_without_env(monkeypatch):
    monkeypatch.delenv("SOLVENT_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SOLVENT_TG_CHAT_ID", raising=False)
    assert alerts.alert("hello") is False


def test_alert_swallows_transport_error(monkeypatch):
    monkeypatch.setenv("SOLVENT_TG_BOT_TOKEN", "token")
    monkeypatch.setenv("SOLVENT_TG_CHAT_ID", "chat")

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(alerts.httpx, "post", boom)
    assert alerts.alert("hello") is False  # caught, not raised
