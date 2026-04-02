---
title: "Backtester v1.0 — Audit Report"
created: 2026-04-02
tags:
  - audit
  - review
  - backtester-v1
status: resolved
---

# Backtester v1.0 — Audit Report

> **Date**: 2026-04-02
> **Auditors**: 4 parallel Opus critic agents (roadmap+cross-plan, data pipeline, engine+strategy, analytics+validation)
> **Overall Verdict**: ACCEPT-WITH-RESERVATIONS (plans are solid; targeted fixes applied)

## Findings Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 2 | Fixed |
| MAJOR | 8 | Fixed |
| MINOR | ~18 | Key items fixed, remainder tracked |

## Critical Issues (Fixed)

### C1. Adverse Selection Formula Inconsistency (Phase 5)
**Problem**: Section 1.3 metrics table uses unclamped `mean(sign(side) * (mid_{t+h} - mid_fill))` while Section 2.2 P&L decomposition uses `min(0, ...)` clamped values. These produce different numbers and the accounting identity check would fail.
**Fix**: Documented two conventions explicitly — clamped for decomposition (AS is always a cost), unclamped for Glosten-Harris analysis (shows full distribution). Added reconciliation note.

### C2. Inventory/Resolution P&L Double-Counting (Phase 5)
**Problem**: If inventory MTM runs through resolution, it captures `position * ($1.00 - last_mid)`. Resolution P&L then adds `position * $1.00` on top — double-counting the terminal move.
**Fix**: Defined boundary: inventory P&L stops at last timestamp before resolution. Resolution P&L = `position * (settlement_price - last_mid)`. Added explicit boundary definition.

## Major Issues (Fixed)

### M1/M2. Strategy Interface Mismatch (Phase 3 ↔ Phase 4)
**Problem**: Phase 3 says "reuse directly" for `strategy/interface.py`. Phase 4 replaces it with a new ABC (2→5 callbacks, different signatures). Also conflicting callback models: per-strike (Phase 3) vs all-strikes-at-once (Phase 4).
**Fix**: Phase 3 updated to mark `interface.py` as ADAPT. Phase 4's all-strikes callback model adopted as canonical. Phase 3 must batch updates before delivering to strategy. Migration note added.

### M3. AMEND OrderActionType Unsupported (Phase 4)
**Problem**: Phase 4 introduces `AMEND` but Phase 3 has no state machine transition and Polymarket doesn't support atomic amends.
**Fix**: AMEND removed from Phase 4. Documented as cancel+resubmit pattern.

### M4. Float-to-Integer Conversion Boundary Unowned (Phase 2→3)
**Problem**: Phase 2 DataProvider returns floats, Phase 3 engine expects integer ticks. No task builds the adapter.
**Fix**: Added explicit task to Phase 3 for building `bt_engine/data/adapter.py`.

### M5. Compression Format Contradiction (Roadmap ↔ Phase 1)
**Problem**: Roadmap says Snappy everywhere, Phase 1 says zstd for ThetaData / Snappy for Telonex.
**Fix**: Roadmap updated to match Phase 1's more considered approach.

### M6. Missing Statistical Validation (Phase 5)
**Problem**: Walk-forward analysis, Monte Carlo, bootstrap confidence intervals documented in existing research but absent from Phase 5.
**Fix**: Added Task 5.11 (Statistical Validation) referencing Performance-Metrics-and-Pitfalls.md Sections 4.1-4.3.

### M7. No B-L Accuracy Validation Test (Phase 5)
**Problem**: No test verifies B-L probabilities against actual binary outcomes.
**Fix**: Added Test 6.8 (B-L Probability Accuracy) using NVDA March 30 data with known outcomes.

### M8. No Error Case Tests (Phase 5)
**Problem**: No tests for B-L failure, empty books, degraded data, near-close fills.
**Fix**: Added Tests 6.9 (Degraded Data Handling) and 6.10 (Edge-of-Day Adverse Selection).

## Minor Issues (Key Items Fixed)

- Phase 5 frontmatter dependency labels corrected
- NDX annotated as "(excluded in v1.0)" in [[ROADMAP]]
- EXPIRED order status added to [[Phase-3-Core-Engine]] state machine description
- Phase 3 task 3.5 updated to include OrderStatus enum change

## Tracked but Not Fixed (Low Priority)

- MicroPriceFairValue scope ([[Phase-4-Fair-Value-Strategy]] stretch goal vs deliverable) — leave as stretch goal
- config.toml extensions per phase — document as cross-cutting concern
- Performance budget (target backtest duration) — defer to implementation
- Visualization tasks — defer to post-v1.0
- Multi-day settlement carryover details — defer to implementation
- Fee model for taker fees — document as known gap
- [[Phase-2-Data-Alignment]] ETL checkpoint mechanism — add during implementation
- Regression testing framework — add during [[Phase-5-Analytics-Validation]] implementation

## Open Questions

1. Does Telonex SDK output per-token-side Parquet files or combined? (Affects [[Phase-2-Data-Alignment]] merge algorithm)
2. How does [[Phase-3-Core-Engine]] engine iterate events from DataProvider? Iterator vs next_event()? (Interface detail for Phase 2↔3 integration)
3. FairValuePricer Protocol signature change between existing code and [[Phase-4-Fair-Value-Strategy]] — Phase 4 is the canonical version

## Conclusion

The plans are exceptionally well-crafted for a research backtesting system. The critical and major issues were all integration seams between phases, not fundamental design flaws. All issues have been resolved in the plan documents. The plans are ready for implementation.

---

**Referenced Plans**: [[ROADMAP]] | [[Phase-1-Data-Acquisition]] | [[Phase-2-Data-Alignment]] | [[Phase-3-Core-Engine]] | [[Phase-4-Fair-Value-Strategy]] | [[Phase-5-Analytics-Validation]]
