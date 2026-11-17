# Live example: memories written by Claude Code

This store wasn't scripted. Claude Code (model: Sonnet, headless `claude -p`) worked in a copy of tinyshop with
Receipts installed (`receipts install`: MCP server plus hooks). Everything below is the agent's own tool calls,
extracted from the session logs.

**Session 1: onboarding.** The prompt:

> You are onboarding onto this repository (tinyshop). Read the code, then use the receipts MCP tools to remember 5 to 7
> durable, specific facts a future session would need (how money is represented, how totals, discounts, tax and
> refunds work, where config lives). Cite the exact symbols or quote the exact config lines as receipts, and use
> derived_from where one fact builds on another. Finish by calling memory_status and give a one-paragraph summary.

**Between sessions:** a teammate's commit switches `Money` to `Decimal`, moves discounts to `promotions.py`, and
raises tax to 8.25% (the same change as the scripted demo).

**Session 2: a fresh session with a question its old memory would get wrong.** The prompt:

> A customer bought $100 of goods with a 10% discount and returns a third of it. How much is the order total, and how
> is the partial refund calculated? Use the receipts memory tools first. If any memory is stale or suspect, verify it
> against the code and repair it (reaffirm, revise or retract) before you answer.

Before the agent read a single file, the SessionStart hook told it that all 7 memories no longer matched the code,
and why. The agent re-read the code, then revised 6 memories and reaffirmed 1 with a note. (Its final chat answer was
cut off by the account's usage limit at that moment. The memory repairs, which are the point, all went through.)

Inspect it:

```bash
receipts --repo examples/live-claude-code/tinyshop list
receipts --repo examples/live-claude-code/tinyshop why M12
```

or in the browser: <https://karthik7271.github.io/receipts/?data=live-session.cypher>

---

## Session 1: tool calls

HOOK SessionStart:
```
Receipts memory is empty for this repo. When you learn something durable about this codebase (an invariant, convention, gotcha, decision), save it with the `remember` tool and cite the code that proves it.
```

- `remember` {"text": "Money is stored as integer cents; Money.__init__ raises TypeError for non-int, and Money.from_dollars takes a string (not float) and truncates to 2 decimals. Never pass floats as amounts.", "cites": [{"symbol": "tinyshop/money.py::Money"}, {"path": "tinyshop/money.py", "quote": "raise TypeError(\"Money is stored as integer cents\")"}], "kind": "convention", "tags": ["money"]}
- `remember` {"text": "All fractional money math (discount, tax, refund) goes through Money.times, which does round(cents * factor) – Python banker's rounding (half to even), not half-up.", "cites": [{"path": "tinyshop/money.py", "quote": "return Money(round(self.cents * factor))"}], "kind": "gotcha", "tags": ["money", "rounding"]}
- `remember` {"text": "Discounts are integer percents applied to the pre-tax subtotal and capped at MAX_DISCOUNT_PERCENT = 50; apply_discount raises ValueError outside 0..50.", "cites": [{"symbol": "tinyshop/pricing.py::apply_discount"}, {"path": "tinyshop/pricing.py", "quote": "MAX_DISCOUNT_PERCENT = 50"}], "kind": "fact", "tags": ["discount"]}
- `remember` {"text": "Settings live in config/settings.yaml, parsed by a hand-rolled flat key: value loader (no YAML lib; no nesting, '#' comments stripped, values with '.' become float, else int, else str). Current lines: tax_rate: 0.08, currency: USD, free_shipping_over: 50.", "cites": [{"path": "config/settings.yaml", "quote": "tax_rate: 0.08"}, {"path": "config/settings.yaml", "quote": "free_shipping_over: 50"}, {"symbol": "tinyshop/settings.py::load_settings"}], "kind": "fact", "tags": ["config"]}
- `remember` {"text": "checkout.total = sum(items) -> apply_discount -> add tax_for(discounted, tax_rate), so tax is charged on the DISCOUNTED amount; tax_rate is read from settings on every call (load_settings, no caching). Builds on the discount cap and the settings file.", "cites": [{"symbol": "tinyshop/checkout.py::total"}, {"path": "tinyshop/checkout.py", "quote": "return discounted + tax_for(discounted, rate)"}], "derived_from": ["M3", "M4"], "kind": "fact", "tags": ["totals", "tax"]}
- `remember` {"text": "refund_amount(paid, returned_fraction) prorates paid.times(fraction) (so rounds per Money.times), requires 0 < fraction <= 1 else ValueError. It refunds a fraction of what was PAID (tax included); there is no separate tax/shipping refund logic.", "cites": [{"symbol": "tinyshop/refunds.py::refund_amount"}, {"path": "tinyshop/refunds.py", "quote": "if not 0 < returned_fraction <= 1:"}], "derived_from": ["M2"], "kind": "fact", "tags": ["refunds"]}
- `remember` {"text": "Gotcha: settings keys `currency` and `free_shipping_over` are NOT read by any code; currency is hardcoded as CURRENCY = \"USD\" in money.py and checkout.total has no shipping logic. Changing those yaml lines has no effect.", "cites": [{"path": "tinyshop/money.py", "quote": "CURRENCY = \"USD\""}, {"path": "config/settings.yaml", "quote": "free_shipping_over: 50"}, {"symbol": "tinyshop/checkout.py::total"}], "derived_from": ["M4"], "kind": "gotcha", "tags": ["config", "dead-config"]}
- `memory_status` {}

turns: 20, cost: $0.27

## Session 2: tool calls

HOOK SessionStart:
```
Receipts: 7 memories about this repo (0 fresh). Call `recall` before relying on what you think you know about this codebase.
Since the last sync, 7 files changed.
These memories no longer match the code. Don't trust them as written; `verify` then `reaffirm`/`revise`/`retract` when you touch the related code:
- M1 [STALE] Money is stored as integer cents; Money.__init__ raises TypeError for non-int, and Money.from_dollars takes a string (not float) and truncates to 2 decimals. Never pass floats as amounts.  (Money changed; quoted text is gone from tinyshop/money.py)
- M2 [STALE] All fractional money math (discount, tax, refund) goes through Money.times, which does round(cents * factor) – Python banker's rounding (half to even), not half-up.  (quoted text is gone from tinyshop/money.py)
- M3 [STALE] Discounts are integer percents applied to the pre-tax subtotal and capped at MAX_DISCOUNT_PERCENT = 50; apply_discount raises ValueError outside 0..50.  (quoted text is gone from tinyshop/pricing.py)
- M4 [STALE] Settings live in config/settings.yaml, parsed by a hand-rolled flat key: value loader (no YAML lib; no nesting, '#' comments stripped, values with '.' become float, else int, else str). Current lines: tax_rate: 0.08, currency: USD, free_shipping_over: 50.  (quoted text is gone from config/settings.yaml)
- M5 [STALE] checkout.total = sum(items) -> apply_discount -> add tax_for(discounted, tax_rate), so tax is charged on the DISCOUNTED amount; tax_rate is read from settings on every call (load_settings, no caching). Builds on the discount cap and the settings file.  (total changed; builds on M4 (stale), M3 (stale))
- M6 [SUSPECT] refund_amount(paid, returned_fraction) prorates paid.times(fraction) (so rounds per Money.times), requires 0 < fraction <= 1 else ValueError. It refunds a fraction of what was PAID (tax included); there is no separate tax/shipping refund logic.  (refund_amount depends on Money.times, whose signature changed; builds on M2 (stale))
- M7 [STALE] Gotcha: settings keys `currency` and `free_shipping_over` are NOT read by any code; currency is hardcoded as CURRENCY = "USD" in money.py and checkout.total has no shipping logic. Changing those yaml lines has no effect.  (total changed; builds on M4 (stale))
```

- `recall` {"question": "money discount tax refund calculation"}
- `revise` {"id": "M1", "text": "Money stores a Decimal dollar amount quantized to the cent with ROUND_HALF_UP (Money.__init__ accepts Decimal or str). Money.from_dollars takes a str. Not integer cents anymore.", "cites": [{"path": "tinyshop/money.py", "quote": "self.amount = Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)"}]}
- `revise` {"id": "M2", "text": "All fractional money math (discount, tax, refund) goes through Money.times, which multiplies the Decimal amount by Decimal(factor) and re-quantizes to the cent with ROUND_HALF_UP (half-up, not banker's rounding). Float factors are converted via Decimal(float), so they carry binary-float noise before rounding.", "cites": [{"path": "tinyshop/money.py", "quote": "return Money(self.amount * Decimal(factor))"}]}
- `revise` {"id": "M3", "text": "Discounts live in tinyshop/promotions.py (not pricing.py). discount_for(subtotal, percent) takes an integer percent applied to the pre-tax subtotal, capped at MAX_DISCOUNT_PERCENT = 50, and raises ValueError outside 0..50. It returns the discounted amount (subtotal.times(1 - percent/100)), not the discount itself.", "cites": [{"path": "tinyshop/promotions.py", "quote": "MAX_DISCOUNT_PERCENT = 50"}, {"path": "tinyshop/promotions.py", "quote": "return subtotal.times(1 - percent / 100)"}]}
- `revise` {"id": "M4", "text": "Settings live in config/settings.yaml, parsed by a hand-rolled flat key: value loader in tinyshop/settings.py (no YAML lib; '#' comments stripped; values with '.' become float, else int, else str). Current lines: tax_rate: 0.0825, currency: USD, free_shipping_over: 50.", "cites": [{"path": "config/settings.yaml", "quote": "tax_rate: 0.0825"}, {"path": "tinyshop/settings.py", "quote": "out[key] = float(value) if \".\" in value else int(value)"}]}
- `revise` {"id": "M5", "text": "checkout.total(items, discount_percent) = sum(items) -> promotions.discount_for -> add pricing.tax_for(discounted, tax_rate). Tax is charged on the DISCOUNTED amount; tax_rate is read from settings on every call (load_settings, no caching).", "cites": [{"path": "tinyshop/checkout.py", "quote": "return discounted + tax_for(discounted, rate)"}]}
- `revise` {"id": "M6", "text": "refund_amount(paid, returned_fraction) returns paid.times(fraction), so it rounds half-up per Money.times. It requires 0 < fraction <= 1, else ValueError. It refunds a fraction of what was PAID (discount and tax already baked in); there is no separate tax or shipping refund logic.", "cites": [{"path": "tinyshop/refunds.py", "quote": "return paid.times(returned_fraction)"}]}
- `reaffirm` {"id": "M7", "note": "Re-read checkout.py, money.py, settings.py: currency and free_shipping_over are still unread by code; CURRENCY is hardcoded and total has no shipping logic."}

turns: 20, cost: $0.25

