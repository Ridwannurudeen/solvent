"""Verify receipt/journal evidence against the published policy manifest."""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct

from ..brain.proof import sha256_json
from ..exec.executor import Journal
from ..kernel.allowlist import ADDRESSES, FLOOR_SYMBOLS, SLEEVE_SYMBOLS
from ..kernel.rules import RISK_PROFILE_NAMES
from ..receipts.chain import GENESIS_HASH, verify_chain
from .manifest import SIGNING_PREFIX

_PROFILE_ORDER = ("safety", "conviction_50", "tournament_50", "tournament_60")
_EXECUTABLE = set(ADDRESSES)
_FLOOR = set(FLOOR_SYMBOLS)
_SLEEVE = set(SLEEVE_SYMBOLS)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _check(
    checks: list[dict], name: str, ok: bool, detail: str, *, required: bool = True
) -> None:
    checks.append({"name": name, "ok": ok, "required": required, "detail": detail})


def _journal_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _allowed_profile(active: str | None, manifest: dict) -> bool:
    if active not in RISK_PROFILE_NAMES:
        return False
    configured = manifest.get("strategy", {}).get("profile")
    if manifest.get("strategy", {}).get("adaptive_profile_enabled") is not True:
        return active == configured
    if configured not in _PROFILE_ORDER:
        return False
    return _PROFILE_ORDER.index(active) <= _PROFILE_ORDER.index(configured)


def _intent_symbols_ok(intent: dict) -> bool:
    from_sym = intent.get("from")
    to_sym = intent.get("to")
    if from_sym not in _EXECUTABLE or to_sym not in _EXECUTABLE:
        return False
    if from_sym not in _FLOOR and from_sym not in _SLEEVE:
        return False
    return to_sym in _FLOOR or to_sym in _SLEEVE


def _max_drawdown(entries: list[dict]) -> float:
    peak = 0.0
    max_dd = 0.0
    for entry in entries:
        equity = float(entry.get("receipt", {}).get("equity_usd") or 0.0)
        if equity <= 0:
            continue
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - equity / peak)
    return max_dd


def _anchor_coverage(entries: list[dict], anchors: object) -> dict:
    anchors = anchors if isinstance(anchors, dict) else {}
    sorted_anchors = sorted(
        (
            {"day": day, **record}
            for day, record in anchors.items()
            if isinstance(record, dict)
        ),
        key=lambda item: item["day"],
        reverse=True,
    )
    latest = sorted_anchors[0] if sorted_anchors else None
    hash_to_seq = {entry["hash"]: entry["receipt"]["seq"] for entry in entries}
    anchored_seq = None
    if latest and latest.get("head_hash") in hash_to_seq:
        anchored_seq = hash_to_seq[latest["head_hash"]]
    anchored_count = anchored_seq + 1 if anchored_seq is not None else 0
    return {
        "local_count": len(entries),
        "local_head_hash": entries[-1]["hash"] if entries else GENESIS_HASH,
        "anchored_count": anchored_count,
        "anchored_seq": anchored_seq,
        "anchored_head_hash": latest.get("head_hash") if latest else None,
        "latest_anchor": latest,
        "anchor_matches_local_log": anchored_seq is not None,
        "unanchored_count": max(0, len(entries) - anchored_count),
    }


def policy_compliance_report(
    data_dir: Path,
    *,
    policy_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    policy_path = policy_path or data_dir / "policy-manifest.json"
    checks: list[dict] = []
    policy = _read_json(policy_path, {})
    manifest = policy.get("manifest") if isinstance(policy, dict) else None
    manifest = manifest if isinstance(manifest, dict) else {}
    manifest_hash = policy.get("manifest_hash") if isinstance(policy, dict) else None
    expected_hash = sha256_json(manifest) if manifest else None

    _check(
        checks,
        "policy_manifest_present",
        bool(manifest),
        str(policy_path) if manifest else "missing policy-manifest.json",
    )
    _check(
        checks,
        "policy_hash_matches_manifest",
        bool(manifest_hash and expected_hash and manifest_hash == expected_hash),
        f"manifest_hash={manifest_hash} expected={expected_hash}",
    )

    signature = policy.get("signature") if isinstance(policy, dict) else None
    signer = None
    if isinstance(signature, dict) and signature.get("signature"):
        try:
            signer = Account.recover_message(
                encode_defunct(text=f"{SIGNING_PREFIX}\n{manifest_hash}"),
                signature=signature["signature"],
            )
        except Exception:
            signer = None
    wallet = manifest.get("agent", {}).get("wallet")
    declared_signer = signature.get("signer") if isinstance(signature, dict) else None
    _check(
        checks,
        "policy_signature_recovers_declared_signer",
        bool(
            signer
            and declared_signer
            and signer.lower() == str(declared_signer).lower()
        ),
        f"recovered={signer} declared={declared_signer}",
    )
    _check(
        checks,
        "policy_signature_matches_trading_wallet",
        bool(signer and wallet and signer.lower() == str(wallet).lower()),
        f"signer={signer} wallet={wallet}",
        required=False,
    )
    anchor = policy.get("anchor") if isinstance(policy, dict) else None
    _check(
        checks,
        "policy_anchor_present",
        bool(isinstance(anchor, dict) and anchor.get("tx_hash")),
        f"tx_hash={anchor.get('tx_hash') if isinstance(anchor, dict) else None}",
    )

    receipts_path = data_dir / "receipts.jsonl"
    chain_ok, receipt_count, head_hash = (
        verify_chain(receipts_path)
        if receipts_path.exists()
        else (True, 0, GENESIS_HASH)
    )
    entries = _load_entries(receipts_path)
    _check(
        checks,
        "receipt_chain_integrity",
        chain_ok,
        f"{receipt_count} receipts, head {head_hash}",
    )

    anchors = _read_json(data_dir / "anchors.json", {})
    coverage = _anchor_coverage(entries, anchors)
    _check(
        checks,
        "latest_anchor_matches_local_log",
        coverage["local_count"] == 0 or coverage["anchor_matches_local_log"],
        f"unanchored_count={coverage['unanchored_count']}",
        required=False,
    )

    scope_start = _parse_ts(manifest.get("generated_at"))
    scoped_entries = []
    for entry in entries:
        ts = _parse_ts(entry.get("receipt", {}).get("ts"))
        if scope_start is None or ts is None or ts >= scope_start:
            scoped_entries.append(entry)

    risk = manifest.get("strategy", {}).get("risk_config", {})
    max_trade_frac = float(risk.get("max_trade_frac") or 0.0)
    x402_max = float(manifest.get("data", {}).get("x402_max_per_call_usd") or 0.0)
    x402_budget = float(manifest.get("data", {}).get("x402_session_budget_usd") or 0.0)
    outcomes = set(manifest.get("execution", {}).get("result_outcomes") or [])
    pretrade_required = bool(
        manifest.get("verification", {}).get("pre_trade_anchor_required")
    )
    daily_spend: dict[str, float] = defaultdict(float)
    executed_now = 0

    for entry in scoped_entries:
        receipt = entry.get("receipt", {})
        phase = receipt.get("phase", "cycle_summary")
        active_profile = receipt.get("signals", {}).get("active_risk_profile")
        if active_profile is not None:
            _check(
                checks,
                f"receipt_{receipt.get('seq')}_profile_allowed",
                _allowed_profile(active_profile, manifest),
                f"active_risk_profile={active_profile}",
            )
        for purchase in receipt.get("data_purchases") or []:
            cost = float(purchase.get("cost_usdc") or 0.0)
            if purchase.get("ok") and cost > 0:
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_data_response_committed",
                    bool(purchase.get("response_hash"))
                    and int(purchase.get("response_bytes") or 0) > 0,
                    f"tool={purchase.get('tool')} cost={cost}",
                )
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_data_cost_within_cap",
                    x402_max <= 0 or cost <= x402_max,
                    f"cost={cost} cap={x402_max}",
                )
                day = str(receipt.get("ts", ""))[:10]
                daily_spend[day] += cost
        for intent in receipt.get("intents") or []:
            _check(
                checks,
                f"receipt_{receipt.get('seq')}_intent_symbols_allowed",
                _intent_symbols_ok(intent),
                f"{intent.get('from')}->{intent.get('to')}",
            )
            notional = float(intent.get("notional_usd") or 0.0)
            equity = float(receipt.get("equity_usd") or 0.0)
            if equity > 0 and notional > 0:
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_intent_size_within_policy",
                    max_trade_frac <= 0 or notional <= equity * max_trade_frac + 0.01,
                    f"notional={notional} equity={equity} max_frac={max_trade_frac}",
                )
        if phase == "execution_seal":
            seal = receipt.get("execution_seal") or {}
            outcome = seal.get("outcome")
            _check(
                checks,
                f"receipt_{receipt.get('seq')}_execution_outcome_allowed",
                outcome in outcomes,
                f"outcome={outcome}",
            )
            if seal.get("applies_state_change"):
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_state_change_is_executed_now",
                    outcome == "executed_now",
                    f"outcome={outcome}",
                )
            if outcome == "executed_now":
                executed_now += 1
                verification = seal.get("verification") or {}
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_settlement_verified",
                    bool(
                        verification
                        and verification.get("status") == 1
                        and verification.get("tx_hash") == seal.get("tx_hash")
                        and verification.get("from_transfer_out", 0) > 0
                        and verification.get("to_transfer_in", 0) > 0
                    ),
                    f"tx_hash={seal.get('tx_hash')}",
                )
                _check(
                    checks,
                    f"receipt_{receipt.get('seq')}_pretrade_anchor_present",
                    not pretrade_required or bool(seal.get("pre_trade_anchor_tx_hash")),
                    f"pre_trade_anchor_tx_hash={seal.get('pre_trade_anchor_tx_hash')}",
                )

    for day, spent in sorted(daily_spend.items()):
        _check(
            checks,
            f"x402_daily_spend_within_budget_{day}",
            x402_budget <= 0 or spent <= x402_budget,
            f"spent={spent:.6f} budget={x402_budget:.6f}",
        )

    journal = Journal(data_dir / "journal.jsonl")
    pending = journal.pending_entries()
    _check(
        checks,
        "journal_has_no_unresolved_attempts",
        not pending,
        f"unresolved={len(pending)}",
    )
    for entry in _journal_lines(data_dir / "journal.jsonl"):
        if entry.get("state") != "CONFIRMED" or str(
            entry.get("tx_hash", "")
        ).startswith("paper-"):
            continue
        entry_ts = _parse_ts(entry.get("ts"))
        if scope_start is not None and entry_ts is not None and entry_ts < scope_start:
            continue
        _check(
            checks,
            f"journal_{entry.get('key')}_confirmed_has_verification",
            bool(entry.get("verification")),
            f"tx_hash={entry.get('tx_hash')}",
        )

    state = _read_json(data_dir / "state.json", {})
    required_checks = [check for check in checks if check["required"]]
    passed_required = sum(1 for check in required_checks if check["ok"])
    compliance_rate = passed_required / len(required_checks) if required_checks else 1.0
    return {
        "schema": "solvent.policy-compliance.v1",
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "ok": all(check["ok"] for check in required_checks),
        "policy_hash": manifest_hash,
        "scope_start": scope_start.isoformat() if scope_start else None,
        "checks": checks,
        "passport": {
            "agent": manifest.get("agent", {}),
            "strategy_profile": manifest.get("strategy", {}).get("profile"),
            "adaptive_profile_enabled": manifest.get("strategy", {}).get(
                "adaptive_profile_enabled"
            ),
            "receipt_count": receipt_count,
            "scoped_receipt_count": len(scoped_entries),
            "head_hash": head_hash,
            "anchor_coverage": coverage,
            "executed_now_count": executed_now,
            "unresolved_execution_count": len(pending),
            "max_observed_drawdown_pct": round(_max_drawdown(entries), 6),
            "runtime_status": state.get("runtime_status", "ACTIVE"),
            "policy_compliance_rate": round(compliance_rate, 6),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = policy_compliance_report(args.data_dir, policy_path=args.policy)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
