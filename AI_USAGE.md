# AI Usage

AI tooling was used as an engineering assistant for:

- Planning the Track 1 system architecture and risk posture.
- Implementing and reviewing Python agent code, tests, ops units, and docs.
- Auditing hackathon requirement alignment and submission readiness.
- Drafting README, runbook, demo, and submission copy.
- Running local/VPS validation loops and interpreting failures.

Human-controlled decisions and actions:

- Track selection, project framing, and trading strategy approval.
- Wallet funding, key custody, and any real on-chain transaction approval.
- VPS deployment decisions and production live-mode cutover.
- GitHub visibility, demo publication, social posts, and DoraHacks submission.

Validation performed:

- Python tests and lint checks are run before commits.
- Live CMC/x402 and TWAK behavior is verified with isolated proof runs before
  being described as working.
- Public receipts are independently verifiable through `verify_receipts.py`.

No AI attribution footers or co-author metadata are included in code, commits,
or submission artifacts.
