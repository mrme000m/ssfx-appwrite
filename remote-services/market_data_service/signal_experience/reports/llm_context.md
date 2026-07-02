# Signal Experience Context

Generated: 2026-07-02T06:42:06.523113+00:00

## Routing Rules
- Allow normal execution for authors: Liam, Grace, Mark, Jason, Matthew, William, Julian.
- Highest-confidence UTC hours: 15, 11, 8.
- Reduce size or block signals with corrupted SL/TP, missing author, or promotional text.

## Top Authors
- Liam: win_rate=0.95, expectancy=84.37 pips, signals=130, streak=0 → allow
- Grace: win_rate=0.98, expectancy=84.19 pips, signals=82, streak=0 → allow
- Mark: win_rate=0.97, expectancy=66.48 pips, signals=121, streak=0 → allow
- Jason: win_rate=0.96, expectancy=58.3 pips, signals=40, streak=3 → allow
- Matthew: win_rate=0.93, expectancy=19.35 pips, signals=197, streak=2 → allow

## Best Sessions (UTC hour)
- hour 15: win_rate=1, signals=34
- hour 11: win_rate=1, signals=42
- hour 8: win_rate=0.98, signals=65
- hour 14: win_rate=0.97, signals=71
- hour 10: win_rate=0.96, signals=44

## Top Patterns
- XAUUSD_SELL_MARKET: win_rate=0.96, expectancy=76.35
- XAUUSD_SELL_LIMIT: win_rate=0.9, expectancy=-8.61
- XAUUSD_BUY_LIMIT: win_rate=0.92, expectancy=None
- XAUUSD_BUY_MARKET: win_rate=0.9, expectancy=None

## Bad-Signal Heuristics
- Missing SL or TP on a NEW signal.
- Corrupted SL/TP (e.g. SL above entry for BUY).
- Messages containing 'GOLD' without clear XAUUSD context.
- Promotional or chat messages (copier, discount, invalid parameters).
