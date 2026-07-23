# Option Strategy Lab

Status: priority product track.

## Current MVP

The Options page includes a frontend-only **Strategy Lab**:

- Load common templates: Long Call, Cash-Secured Put, Bull Put Spread, Bear
  Call Spread, Iron Condor, Long Straddle.
- Edit side, call/put, strike, premium, and contract count per leg.
- Calculate net debit/credit, sampled max profit, sampled max loss, approximate
  breakevens, and expiration payoff.
- No third-party market data is required; all inputs are manual.

This is intentionally safe for sharing because it does not store trades, send
orders, or fetch external option chains.

## Next Iteration

To make this a real strategy lab rather than a manual calculator:

- Add market-data endpoints for ticker search, underlying quote, expirations,
  and option chains.
- Prefer IBKR Client Portal / Gateway when available; otherwise make any
  external data provider explicit and optional.
- Auto-select strikes from strategy templates relative to spot.
- Use bid/ask/mid/last consistently and display which price basis is used.
- Add Black-Scholes "today" curve, IV multiplier, DTE slider, and Greeks.
- Persist no strategy output by default; keep it as a scratchpad until the user
  explicitly saves a note or scenario.

## Non-goals

- No order placement.
- No recommendation engine.
- No copying of private external app bundles, endpoints, account feeds, or
  subscription features.
