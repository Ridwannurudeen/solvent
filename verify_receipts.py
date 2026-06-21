#!/usr/bin/env python3
"""Standalone receipt-chain verifier — Python stdlib only, no install, zero trust.

Pulls SOLVENT's public decision log in bounded pages and recomputes the entire
hash chain from genesis, independent of the agent's own code. Prints the head
hash so you can compare it against the /verify endpoint and the on-chain
ERC-8004 anchor.

    python verify_receipts.py                                   # fetch the live log
    python verify_receipts.py https://solvent.gudman.xyz/receipts
    python verify_receipts.py receipts.json                     # a saved JSON array

Each entry is {"receipt": {...}, "hash": "0x..."} and commits to the previous
entry's hash via receipt.prev_hash, so altering any receipt breaks every hash
after it. The receipt hash is sha256 over the canonical JSON of the receipt
(sorted keys, no whitespace) — recomputed here from scratch.
"""

import hashlib
import json
import sys
import urllib.request

GENESIS = "0x" + "0" * 64
DEFAULT = "https://solvent.gudman.xyz/receipts"
PAGE_SIZE = 1000


def page_url(base: str, start: int) -> str:
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}start={start}&limit={PAGE_SIZE}"


def load_paged(base: str) -> list:
    entries = []
    start = 0
    while True:
        with urllib.request.urlopen(page_url(base, start)) as r:
            page = json.load(r)
        entries.extend(page)
        if len(page) < PAGE_SIZE:
            return entries
        start += len(page)


def load(src: str) -> list:
    if src.startswith("http"):
        if "start=" not in src:
            return load_paged(src)
        with urllib.request.urlopen(src) as r:
            return json.load(r)
    with open(src, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    entries = load(src)
    prev = GENESIS
    for i, e in enumerate(entries):
        rec = e["receipt"]
        seq = rec.get("seq", i)
        canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        recomputed = "0x" + hashlib.sha256(canonical.encode()).hexdigest()
        if rec["prev_hash"] != prev:
            print(
                f"BROKEN at seq {seq}: prev_hash does not link to the previous receipt"
            )
            return 1
        if recomputed != e["hash"]:
            print(
                f"BROKEN at seq {seq}: receipt hash mismatch — this receipt was altered"
            )
            return 1
        prev = e["hash"]
    print(f"OK - {len(entries)} receipts, chain intact")
    print(f"head: {prev}")
    print("compare this head against /verify and the on-chain ERC-8004 anchor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
