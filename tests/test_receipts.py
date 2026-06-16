import json

from solvent.receipts.chain import (
    GENESIS_HASH,
    DataPurchase,
    ReceiptChain,
    verify_chain,
)


def make_chain(tmp_path, n=3):
    chain = ReceiptChain(tmp_path / "receipts.jsonl")
    for i in range(n):
        chain.append(
            ts=f"2026-06-2{i}T12:00:00Z",
            data_purchases=[
                DataPurchase(tool="get_global_metrics_latest", cost_usdc=0.01, ok=True)
            ],
            signals={"fng": 60 + i},
            regime="risk-on",
            thesis=f"cycle {i}",
            intents=[],
            executions=[],
            equity_usd=300.0 + i,
            dq_headroom_pct=0.30,
        )
    return chain


def test_chain_links_from_genesis(tmp_path):
    chain = make_chain(tmp_path)
    ok, count, head = verify_chain(chain.path)
    assert ok and count == 3 and head == chain.head_hash
    first = json.loads(chain.path.read_text().splitlines()[0])
    assert first["receipt"]["prev_hash"] == GENESIS_HASH


def test_chain_survives_reload(tmp_path):
    chain = make_chain(tmp_path)
    head = chain.head_hash
    reloaded = ReceiptChain(chain.path)
    assert reloaded.head_hash == head
    assert reloaded.next_seq == 3
    reloaded.append(ts="2026-06-24T13:00:00Z", thesis="after reload")
    ok, count, _ = verify_chain(chain.path)
    assert ok and count == 4


def test_tamper_detected(tmp_path):
    chain = make_chain(tmp_path)
    lines = chain.path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["receipt"]["equity_usd"] = 999999.0  # rewrite history
    lines[1] = json.dumps(entry, separators=(",", ":"))
    chain.path.write_text("\n".join(lines) + "\n")
    ok, count, _ = verify_chain(chain.path)
    assert not ok and count == 1


def test_deletion_detected(tmp_path):
    chain = make_chain(tmp_path)
    lines = chain.path.read_text().splitlines()
    chain.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    ok, count, _ = verify_chain(chain.path)
    assert not ok
