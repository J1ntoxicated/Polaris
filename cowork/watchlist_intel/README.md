# Watchlist Intel — Cowork Quickstart (Jin)

1. **Open**: start a Claude Cowork session and point it at this folder
   (`cowork/watchlist_intel/`) — `INSTRUCTIONS.md` is its constitution.
2. **Trigger**: run it in the AEST evening = US pre-market. The session does the
   ~25-30 min routine and writes candidates. Crypto/macro axes (OKX 24/7 +
   Capital CFD) add a daily +10 min routine — see `INSTRUCTIONS_CRYPTO_MACRO.md`
   / `CONTRACT_CRYPTO_MACRO.md`; those are collected-only (bot ignores, fail-safe).
   Macro context (FX · bonds · regime · themes) adds a ~2-min daily block +
   theme deep-scan every-other-day — see `INSTRUCTIONS_CONTEXT.md` /
   `CONTRACT_CONTEXT.md`; also collected-only (allowlist default-deny, fail-safe).
3. **Check output**: `cat data/intel/alpaca_seed.json` — verify `expiry_ts` is in
   the future (past = bot ignores it, fail-safe) and every candidate has an
   evidence URL. See `example_watchlist_intel.json` for the shape.
4. **Check ingest** (bot side, wired 2026-07-04): `python3 -c "from
   polaris.core.universe.intel_seed import load_intel_seed as l; print(l())"`
   — non-empty `seed_tags` means the bot's universe union + signal cohort
   tagging picked the file up. Cohort report: see CONTRACT.md's verify line.
