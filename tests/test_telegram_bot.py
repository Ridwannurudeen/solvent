from urllib.parse import urlparse

from solvent.ops import telegram_bot


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.posts = []
        self.routes = {
            "/state": {
                "runtime_status": "ACTIVE",
                "alive": True,
                "heartbeat_age_s": 65,
                "agent_id": 136384,
                "anchor_network": "bsc-mainnet",
                "start_equity_usd": 45.0,
                "peak_equity_usd": 46.0,
                "holdings": {"USDT": 38.0, "USDC": 6.5},
                "holdings_source": "live-cache",
                "position": None,
            },
            "/verify": {
                "ok": True,
                "count": 46,
                "head_hash": "0x" + "12" * 32,
                "anchor_coverage": {
                    "anchored_count": 39,
                    "unanchored_count": 7,
                    "anchor_matches_local_log": True,
                    "latest_anchor": {"tx_hash": "0x" + "34" * 32},
                },
            },
            "/signal": {
                "signal_hash": "0x" + "56" * 32,
                "signal": {
                    "regime": "risk-off",
                    "active_risk_profile": "safety",
                    "fear_greed": 21,
                    "degraded": False,
                    "thesis": "hold",
                    "equity_usd": 45.8,
                    "dq_headroom_pct": 0.29,
                    "top_momentum": [{"symbol": "UNI", "score": 9.315}],
                },
            },
            "/policy": {
                "manifest_hash": "0x" + "78" * 32,
                "signature": {"signer": "0xabc"},
                "anchor": {"tx_hash": "0x" + "90" * 32},
                "manifest": {
                    "strategy": {
                        "profile": "tournament_60",
                        "adaptive_profile_enabled": True,
                        "risk_config": {
                            "kill_switch_drawdown_pct": 0.22,
                            "dq_drawdown_pct": 0.30,
                            "floor_frac_min": 0.40,
                            "sleeve_frac_target": 0.58,
                            "max_trade_frac": 0.60,
                            "stop_pct": 0.07,
                        },
                    },
                    "execution": {
                        "executor": "Trust Wallet Agent Kit",
                        "chain": "bsc-mainnet",
                    },
                },
            },
            "/policy-compliance": {
                "ok": True,
                "passport": {"unresolved_execution_count": 0},
            },
            "/receipts": [
                {
                    "hash": "0x" + "aa" * 32,
                    "receipt": {
                        "seq": 12,
                        "ts": "2026-06-20T00:00:00+00:00",
                        "intents": [
                            {
                                "from_symbol": "USDT",
                                "to_symbol": "USDC",
                                "notional_usd": 2.0,
                            }
                        ],
                        "executions": [
                            {
                                "outcome": "executed_now",
                                "tx_hash": "0x" + "bb" * 32,
                            }
                        ],
                    },
                }
            ],
        }

    def get(self, url, timeout):
        path = urlparse(url).path
        return _Resp(self.routes[path])

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        return _Resp({"ok": True, "result": True})


def test_set_commands_registers_professional_read_only_menu():
    client = _Client()

    assert telegram_bot.set_commands("token", client) is True

    payload = client.posts[0][1]
    commands = {item["command"] for item in payload["commands"]}
    assert {"status", "portfolio", "signal", "risk", "pnl", "proof"} <= commands
    assert {"buy", "sell", "pause", "resume"}.isdisjoint(commands)


def test_render_status_uses_live_public_state():
    text = telegram_bot.render_command(
        "status", "https://solvent.gudman.xyz", _Client()
    )

    assert "SOLVENT status" in text
    assert "Runtime: ACTIVE" in text
    assert "Receipts: 46" in text
    assert "Chain OK: yes" in text


def test_render_portfolio_and_lasttrade():
    client = _Client()

    portfolio = telegram_bot.render_command(
        "portfolio", "https://solvent.gudman.xyz", client
    )
    trade = telegram_bot.render_command(
        "lasttrade", "https://solvent.gudman.xyz", client
    )

    assert "USDT" in portfolio
    assert "Open sleeve: none" in portfolio
    assert "USDT -> USDC" in trade
    assert "executed_now" in trade


def test_process_update_replies_only_to_configured_chat():
    client = _Client()
    update = {"message": {"chat": {"id": 123}, "text": "/status"}}

    assert (
        telegram_bot.process_update(
            update,
            token="token",
            allowed_chat_id="123",
            public_base="https://solvent.gudman.xyz",
            client=client,
        )
        is True
    )
    assert client.posts[-1][1]["chat_id"] == "123"
    assert "SOLVENT status" in client.posts[-1][1]["text"]

    before = len(client.posts)
    telegram_bot.process_update(
        {"message": {"chat": {"id": 456}, "text": "/status"}},
        token="token",
        allowed_chat_id="123",
        public_base="https://solvent.gudman.xyz",
        client=client,
    )
    assert len(client.posts) == before


def test_plain_message_gets_help_for_authorized_chat():
    client = _Client()

    telegram_bot.process_update(
        {"message": {"chat": {"id": 123}, "text": "hello"}},
        token="token",
        allowed_chat_id="123",
        public_base="https://solvent.gudman.xyz",
        client=client,
    )

    assert "/status" in client.posts[-1][1]["text"]
