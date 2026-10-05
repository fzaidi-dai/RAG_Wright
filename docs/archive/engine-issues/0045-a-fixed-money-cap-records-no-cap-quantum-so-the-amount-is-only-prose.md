# Engine issue 0045: a fixed-money cap records no `cap_quantum`, so the amount exists only as prose

**Raised by:** RuleWright (product), 2026-09-19, while building golden expectations from real extraction (T-5a.12g/h).
**Severity:** a capability gap, not a crash. It blocks any query of the form "capped below X" and makes the
product's own headline question — *"and at what amount?"* — unanswerable from structure.
**Path:** contract extraction, `PropertyDimension.CAP_QUANTUM` on `Cap On Liability`.

---

## What we see

Five short contracts ingested through the product's normal path, read back with `contract_clause_index`:

| document | `cap_basis` | `cap_quantum` |
|---|---|---|
| `acme-msa` — *"shall not exceed the fees paid by the Customer in the twelve (12) months immediately preceding the claim"* | `multiple_of_fees` (EXTRACTED) | **`"the fees paid by the Customer in the twelve (12) months immediately preceding the claim"`** (EXTRACTED) |
| `initech-sow` — *"shall not exceed two hundred and fifty thousand US dollars (USD 250,000)"* | `fixed_fee` (EXTRACTED) | **absent** |

The clause that states a **plain figure** records no quantum. The clause that states a **formula** records
one. That is the opposite of what we expected, and the figure is the easier of the two to extract.

`umbrella-msa` (*"shall not exceed one million US dollars (USD 1,000,000)"*) behaves like `initech-sow`.

## Where it comes from

`contracts/property.py` declares both dimensions and is explicit about the intent:

```python
CAP_BASIS   = "cap_basis"
CAP_QUANTUM = "cap_quantum"  # open-valued (e.g. "12_months", "1x_fees")
```
> `cap_basis` keeps a closed enum (the shape of the cap) while `cap_quantum` carries the light open scalar
> (no structured money object — SPEC section 8).

So a money-typed value is a deliberate non-goal, which we understand. But the *open scalar* is also not
being filled for the fixed-figure case, and that part looks unintended rather than decided: `USD 250,000`
is exactly the "light open scalar" the field describes.

## Why it matters to us

**Our product question is literally "and at what amount?"** (`PR-14` / `AC-13`, and the first thing a
lawyer asks after "which contracts cap liability"). Today we can answer it only by having an answer model
read the clause text back, because nothing asserts the amount as a value.

Two consequences we have already hit:

1. **No threshold queries.** *"Which contracts are capped below USD 500,000?"* is an obvious next question
   and cannot be asked of the graph at all. `cap_basis` tells us the SHAPE; we cannot compare shapes.
2. **We were driven to parse prose.** Our eval derived expected cap amounts by regexing money-shaped
   digits out of an answer paragraph — which read a document-id fragment (`27656071371`) as a cap on its
   first run. We are removing that, and it exists only because no typed value was available.

We are deliberately **not** working around this by pattern-matching `cap_quantum`-shaped text in product
code: parsing a figure out of a clause is extraction, and extraction is the engine's job (`ADR-0052`).

## What would help

In our order of preference:

1. **Populate `cap_quantum` for `fixed_fee` caps** with the figure as written — `"USD 250,000"`,
   `"two hundred and fifty thousand US dollars (USD 250,000)"`, whatever the extractor sees. This stays
   inside SPEC §8 (still a light open scalar, still no money object) and closes the asymmetry.
2. **A normalised numeric alongside it** — e.g. `cap_amount` as `{currency, value}` — if SPEC §8 is open to
   revisiting. This is what makes "capped below X" possible, and we would use it immediately.
3. If neither, **a documented statement that a fixed cap's figure is not extracted**, so we can say so in
   the product rather than appearing not to understand the question.

## Reproduction

Ingest a contract containing:

```
## 7. Limitation of Liability

The aggregate liability of Initech Systems Ltd under this Agreement shall not exceed two hundred and
fifty thousand US dollars (USD 250,000).
```

Then read `contract_clause_index(store, contract_id)` and look for `cap_quantum` on the
`Cap On Liability` clause. Compare against a contract whose cap is a formula, which does record one.

Engine at the commit RuleWright consumes as of 2026-09-19.
