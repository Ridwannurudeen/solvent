"""Read-only Telegram command surface for SOLVENT ops.

The bot answers only the configured chat id. It never signs transactions, never
changes runtime state, and reports only public/read-only SOLVENT evidence.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PUBLIC_BASE = "https://solvent.gudman.xyz"
OFFSET_FILE = "telegram-offset.json"
MAX_MESSAGE_CHARS = 3900

COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Open the SOLVENT command menu"},
    {"command": "help", "description": "Show available read-only commands"},
    {"command": "status", "description": "Runtime, liveness, receipt head"},
    {"command": "portfolio", "description": "Wallet holdings and open sleeve"},
    {"command": "signal", "description": "Current regime and momentum read"},
    {"command": "risk", "description": "Drawdown, halt, and guardrails"},
    {"command": "pnl", "description": "Equity, peak, and drawdown view"},
    {"command": "proof", "description": "Receipt chain and anchor coverage"},
    {"command": "policy", "description": "Frozen policy manifest summary"},
    {"command": "health", "description": "Public proof health checks"},
    {"command": "lasttrade", "description": "Latest execution evidence"},
    {"command": "links", "description": "Public verifier endpoints"},
]

logger = logging.getLogger(__name__)


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(number) >= 100:
        return f"{number:,.2f}"
    return f"{number:,.6f}".rstrip("0").rstrip(".")


def _short(value: Any, size: int = 10) -> str:
    if not isinstance(value, str) or len(value) <= size * 2:
        return str(value or "n/a")
    return f"{value[:size]}...{value[-size:]}"


def _age(seconds: Any) -> str:
    if seconds is None:
        return "n/a"
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "n/a"
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h {minutes % 60}m"
    return f"{hours // 24}d {hours % 24}h"


def _json_get(public_base: str, route: str, client=httpx) -> dict | list:
    url = f"{public_base.rstrip('/')}/{route.lstrip('/')}"
    response = client.get(url, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(f"{route} returned HTTP {response.status_code}")
    return response.json()


def _api(token: str, method: str, payload: dict, client=httpx) -> dict:
    response = client.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=60,
    )
    if response.status_code != 200:
        logger.warning("telegram %s returned HTTP %s", method, response.status_code)
        return {"ok": False}
    try:
        data = response.json()
    except ValueError:
        logger.warning("telegram %s returned non-json response", method)
        return {"ok": False}
    if not data.get("ok"):
        logger.warning("telegram %s failed: %s", method, data.get("description"))
    return data


def set_commands(token: str, client=httpx) -> bool:
    return bool(_api(token, "setMyCommands", {"commands": COMMANDS}, client).get("ok"))


def send_message(token: str, chat_id: str | int, text: str, client=httpx) -> bool:
    return bool(
        _api(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text[:MAX_MESSAGE_CHARS],
                "disable_web_page_preview": True,
            },
            client,
        ).get("ok")
    )


def _help() -> str:
    rows = [f"/{item['command']} - {item['description']}" for item in COMMANDS]
    return "SOLVENT read-only command menu\n\n" + "\n".join(rows)


def _links(public_base: str) -> str:
    base = public_base.rstrip("/")
    return "\n".join(
        [
            "SOLVENT public proof links",
            f"Dashboard: {base}",
            f"Verify: {base}/verify",
            f"State: {base}/state",
            f"Signal: {base}/signal",
            f"Policy: {base}/policy",
            f"Policy compliance: {base}/policy-compliance",
            f"Receipts: {base}/receipts",
            f"Inference checks: {base}/inference-verification",
        ]
    )


def _status(public_base: str, client=httpx) -> str:
    state = _json_get(public_base, "/state", client)
    verify = _json_get(public_base, "/verify", client)
    coverage = verify.get("anchor_coverage") or state.get("anchor_coverage") or {}
    return "\n".join(
        [
            "SOLVENT status",
            f"Runtime: {state.get('runtime_status', 'n/a')}",
            f"Alive: {'yes' if state.get('alive') else 'no'}",
            f"Heartbeat age: {_age(state.get('heartbeat_age_s'))}",
            f"Agent ID: {state.get('agent_id') or 'n/a'}",
            f"Network: {state.get('anchor_network') or 'n/a'}",
            f"Receipts: {verify.get('count', 'n/a')}",
            f"Chain OK: {'yes' if verify.get('ok') else 'no'}",
            f"Head: {_short(verify.get('head_hash'))}",
            f"Unanchored: {coverage.get('unanchored_count', 'n/a')}",
        ]
    )


def _portfolio(public_base: str, client=httpx) -> str:
    state = _json_get(public_base, "/state", client)
    signal = _json_get(public_base, "/signal", client)
    holdings = state.get("holdings") or {}
    rows = [
        "SOLVENT portfolio",
        f"Equity: {_money((signal.get('signal') or {}).get('equity_usd'))}",
        f"Holdings source: {state.get('holdings_source') or 'n/a'}",
    ]
    if state.get("holdings_error"):
        rows.append(f"Holdings error: {state['holdings_error']}")
    if holdings:
        rows.append("Holdings:")
        for symbol, units in sorted(holdings.items()):
            rows.append(f"- {symbol}: {_num(units)}")
    else:
        rows.append("Holdings: n/a")
    position = state.get("position")
    if position:
        rows.append(
            "Open sleeve: "
            f"{position.get('symbol')} notional {_money(position.get('notional_usd'))}"
        )
    else:
        rows.append("Open sleeve: none")
    return "\n".join(rows)


def _signal(public_base: str, client=httpx) -> str:
    payload = _json_get(public_base, "/signal", client)
    signal = payload.get("signal") or {}
    rows = [
        "SOLVENT signal",
        f"Regime: {signal.get('regime') or 'n/a'}",
        f"Profile: {signal.get('active_risk_profile') or 'n/a'}",
        f"Fear & Greed: {signal.get('fear_greed') if signal.get('fear_greed') is not None else 'n/a'}",
        f"Degraded data: {'yes' if signal.get('degraded') else 'no'}",
        f"Thesis: {signal.get('thesis') or 'n/a'}",
        f"Signal hash: {_short(payload.get('signal_hash'))}",
    ]
    top = signal.get("top_momentum") or []
    if top:
        rows.append("Top momentum:")
        for item in top[:5]:
            rows.append(f"- {item.get('symbol')}: {_num(item.get('score'))}")
    return "\n".join(rows)


def _risk(public_base: str, client=httpx) -> str:
    state = _json_get(public_base, "/state", client)
    signal = _json_get(public_base, "/signal", client)
    policy = _json_get(public_base, "/policy", client)
    risk = ((policy.get("manifest") or {}).get("strategy") or {}).get(
        "risk_config"
    ) or {}
    sig = signal.get("signal") or {}
    return "\n".join(
        [
            "SOLVENT risk",
            f"Runtime: {state.get('runtime_status', 'n/a')}",
            f"Halt reason: {state.get('halt_reason') or 'none'}",
            f"DQ headroom: {_pct(sig.get('dq_headroom_pct'))}",
            f"Kill switch: {_pct(risk.get('kill_switch_drawdown_pct'))}",
            f"DQ drawdown gate: {_pct(risk.get('dq_drawdown_pct'))}",
            f"Floor minimum: {_pct(risk.get('floor_frac_min'))}",
            f"Sleeve target: {_pct(risk.get('sleeve_frac_target'))}",
            f"Max trade fraction: {_pct(risk.get('max_trade_frac'))}",
            f"Stop: {_pct(risk.get('stop_pct'))}",
        ]
    )


def _pnl(public_base: str, client=httpx) -> str:
    state = _json_get(public_base, "/state", client)
    signal = _json_get(public_base, "/signal", client)
    equity = (signal.get("signal") or {}).get("equity_usd")
    peak = state.get("peak_equity_usd")
    start = state.get("start_equity_usd")
    drawdown = None
    gain = None
    try:
        if equity is not None and peak:
            drawdown = float(equity) / float(peak) - 1.0
        if equity is not None and start:
            gain = float(equity) / float(start) - 1.0
    except (TypeError, ValueError, ZeroDivisionError):
        drawdown = None
        gain = None
    return "\n".join(
        [
            "SOLVENT PnL",
            f"Current equity: {_money(equity)}",
            f"Start equity: {_money(start)}",
            f"Peak equity: {_money(peak)}",
            f"Return vs start: {_pct(gain)}",
            f"Drawdown vs peak: {_pct(drawdown)}",
        ]
    )


def _proof(public_base: str, client=httpx) -> str:
    verify = _json_get(public_base, "/verify", client)
    coverage = verify.get("anchor_coverage") or {}
    anchor = coverage.get("latest_anchor") or {}
    return "\n".join(
        [
            "SOLVENT proof",
            f"Chain OK: {'yes' if verify.get('ok') else 'no'}",
            f"Receipts: {verify.get('count', 'n/a')}",
            f"Head: {_short(verify.get('head_hash'))}",
            f"Anchored receipts: {coverage.get('anchored_count', 'n/a')}",
            f"Unanchored receipts: {coverage.get('unanchored_count', 'n/a')}",
            f"Latest anchor tx: {_short(anchor.get('tx_hash'))}",
            f"Verify: {public_base.rstrip('/')}/verify",
        ]
    )


def _policy(public_base: str, client=httpx) -> str:
    payload = _json_get(public_base, "/policy", client)
    manifest = payload.get("manifest") or {}
    strategy = manifest.get("strategy") or {}
    execution = manifest.get("execution") or {}
    anchor = payload.get("anchor") or {}
    return "\n".join(
        [
            "SOLVENT policy",
            f"Manifest hash: {_short(payload.get('manifest_hash'))}",
            f"Profile: {strategy.get('profile') or 'n/a'}",
            f"Adaptive profile: {'yes' if strategy.get('adaptive_profile_enabled') else 'no'}",
            f"Executor: {execution.get('executor') or 'n/a'}",
            f"Chain: {execution.get('chain') or 'n/a'}",
            f"Policy anchor: {_short(anchor.get('tx_hash'))}",
            f"Signature: {'present' if payload.get('signature') else 'missing'}",
        ]
    )


def _health(public_base: str, client=httpx) -> str:
    state = _json_get(public_base, "/state", client)
    verify = _json_get(public_base, "/verify", client)
    signal = _json_get(public_base, "/signal", client)
    compliance = _json_get(public_base, "/policy-compliance", client)
    coverage = verify.get("anchor_coverage") or {}
    return "\n".join(
        [
            "SOLVENT health",
            f"Public state alive: {'yes' if state.get('alive') else 'no'}",
            f"Receipt chain OK: {'yes' if verify.get('ok') else 'no'}",
            f"Policy compliance OK: {'yes' if compliance.get('ok') else 'no'}",
            f"Signal hash present: {'yes' if signal.get('signal_hash') else 'no'}",
            f"Anchor matches local log: {'yes' if coverage.get('anchor_matches_local_log') else 'no'}",
            f"Unresolved executions: {(compliance.get('passport') or {}).get('unresolved_execution_count', 'n/a')}",
        ]
    )


def _lasttrade(public_base: str, client=httpx) -> str:
    payload = _json_get(public_base, "/receipts", client)
    if not isinstance(payload, list):
        return "SOLVENT last trade\nReceipts payload unavailable."
    for entry in reversed(payload):
        receipt = entry.get("receipt") or {}
        executions = receipt.get("executions") or []
        seal = receipt.get("execution_seal") or {}
        if executions:
            execution = executions[-1]
            intents = receipt.get("intents") or []
            intent = intents[-1] if intents else {}
            return "\n".join(
                [
                    "SOLVENT last trade",
                    f"Receipt seq: {receipt.get('seq')}",
                    f"Time: {receipt.get('ts')}",
                    f"Intent: {intent.get('from_symbol', '?')} -> {intent.get('to_symbol', '?')}",
                    f"Notional: {_money(intent.get('notional_usd'))}",
                    f"Outcome: {execution.get('outcome') or execution.get('state') or 'n/a'}",
                    f"Tx: {_short(execution.get('tx_hash'))}",
                ]
            )
        if seal:
            return "\n".join(
                [
                    "SOLVENT last trade",
                    f"Receipt seq: {receipt.get('seq')}",
                    f"Time: {receipt.get('ts')}",
                    f"Intent key: {receipt.get('intent_key') or 'n/a'}",
                    f"Outcome: {seal.get('outcome') or 'n/a'}",
                    f"Tx: {_short(seal.get('tx_hash'))}",
                ]
            )
    return "SOLVENT last trade\nNo execution evidence found yet."


def render_command(command: str, public_base: str, client=httpx) -> str:
    cmd = command.lstrip("/").lower()
    try:
        if cmd in {"start", "help"}:
            return _help()
        if cmd == "status":
            return _status(public_base, client)
        if cmd == "portfolio":
            return _portfolio(public_base, client)
        if cmd == "signal":
            return _signal(public_base, client)
        if cmd == "risk":
            return _risk(public_base, client)
        if cmd == "pnl":
            return _pnl(public_base, client)
        if cmd == "proof":
            return _proof(public_base, client)
        if cmd == "policy":
            return _policy(public_base, client)
        if cmd == "health":
            return _health(public_base, client)
        if cmd == "lasttrade":
            return _lasttrade(public_base, client)
        if cmd == "links":
            return _links(public_base)
    except Exception as exc:
        logger.warning("command %s failed: %s", cmd, exc)
        return f"SOLVENT {cmd}\nUnavailable: {exc}"
    return _help()


def _command_from_text(text: str) -> str | None:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not first.startswith("/"):
        return None
    return first[1:].split("@", 1)[0].lower()


def process_update(
    update: dict,
    *,
    token: str,
    allowed_chat_id: str,
    public_base: str,
    client=httpx,
) -> bool:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if chat_id != str(allowed_chat_id):
        return False
    command = _command_from_text(str(message.get("text") or ""))
    if command is None:
        command = "help"
    return send_message(
        token, chat_id, render_command(command, public_base, client), client
    )


def _load_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except ValueError:
        return None
    offset = payload.get("offset") if isinstance(payload, dict) else None
    return int(offset) if offset is not None else None


def _save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"offset": offset}))
    tmp.replace(path)


def poll(
    data_dir: Path,
    *,
    token: str,
    allowed_chat_id: str,
    public_base: str = DEFAULT_PUBLIC_BASE,
    client=httpx,
    once: bool = False,
) -> None:
    set_commands(token, client)
    offset_path = data_dir / OFFSET_FILE
    offset = _load_offset(offset_path)
    while True:
        payload: dict[str, Any] = {"timeout": 50, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        data = _api(token, "getUpdates", payload, client)
        updates = data.get("result") if data.get("ok") else []
        for update in updates:
            update_id = update.get("update_id")
            if update_id is not None:
                offset = max(offset or 0, int(update_id) + 1)
            process_update(
                update,
                token=token,
                allowed_chat_id=allowed_chat_id,
                public_base=public_base,
                client=client,
            )
        if offset is not None:
            _save_offset(offset_path, offset)
        if once:
            return
        if not updates:
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SOLVENT_DATA_DIR", "/opt/solvent/data")),
    )
    parser.add_argument("--public-base", default=os.environ.get("SOLVENT_PUBLIC_BASE"))
    parser.add_argument("--set-commands", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    token = os.environ.get("SOLVENT_TG_BOT_TOKEN")
    chat_id = os.environ.get("SOLVENT_TG_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("SOLVENT_TG_BOT_TOKEN / SOLVENT_TG_CHAT_ID missing")

    public_base = args.public_base or DEFAULT_PUBLIC_BASE
    if args.set_commands:
        return 0 if set_commands(token) else 1
    poll(
        args.data_dir,
        token=token,
        allowed_chat_id=chat_id,
        public_base=public_base,
        once=args.once,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
