"""Review-only scanner for eligible SOLVENT sleeve candidates."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from ..kernel.allowlist import DISCOVERY_SYMBOLS, needs_manual_pin
from ..signals.universe import CandidateScanner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quotes-file", type=Path)
    parser.add_argument("--symbols")
    parser.add_argument("--min-volume-usd", type=float, default=5_000_000.0)
    parser.add_argument("--min-market-cap-usd", type=float, default=25_000_000.0)
    parser.add_argument("--min-momentum", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.symbols:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    else:
        symbols = tuple(s for s in DISCOVERY_SYMBOLS if needs_manual_pin(s))

    quotes = json.loads(args.quotes_file.read_text()) if args.quotes_file else {}
    result = CandidateScanner().scan_quotes(
        quotes,
        symbols=symbols,
        min_volume_usd=args.min_volume_usd,
        min_market_cap_usd=args.min_market_cap_usd,
        min_momentum=args.min_momentum,
    )
    payload = {"candidates": [asdict(candidate) for candidate in result.candidates]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True))
    if args.json or not args.output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
