import json
from pathlib import Path

REG = Path(__file__).resolve().parents[1] / "web" / "agent-registration.json"


def test_registration_exists_and_parses():
    doc = json.loads(REG.read_text())
    assert doc["type"] == "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"


def test_registration_binds_agent_136384_on_bsc():
    doc = json.loads(REG.read_text())
    regs = doc["registrations"]
    assert {
        "agentId": 136384,
        "agentRegistry": "eip155:56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    } in regs


def test_registration_urls_are_https_and_ours():
    doc = json.loads(REG.read_text())
    assert doc["url"] == "https://solvent.gudman.xyz"
    for svc in doc["services"]:
        assert svc["endpoint"].startswith("https://solvent.gudman.xyz")


def test_registration_makes_no_performance_claims():
    text = REG.read_text().lower()
    for banned in ("guaranteed", "profit", "apy", "returns"):
        assert banned not in text
