from datetime import datetime, timedelta, timezone

from solvent.ops.watchdog import heartbeat_age

NOW = datetime(2026, 6, 24, 12, 0, tzinfo=timezone.utc)


def test_missing_heartbeat_returns_none(tmp_path):
    assert heartbeat_age(tmp_path, now=NOW) is None


def test_fresh_heartbeat(tmp_path):
    (tmp_path / "heartbeat").write_text((NOW - timedelta(minutes=10)).isoformat())
    assert heartbeat_age(tmp_path, now=NOW) == 600.0


def test_stale_heartbeat(tmp_path):
    (tmp_path / "heartbeat").write_text((NOW - timedelta(hours=3)).isoformat())
    assert heartbeat_age(tmp_path, now=NOW) == 3 * 3600.0
