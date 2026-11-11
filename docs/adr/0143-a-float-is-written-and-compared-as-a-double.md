# ADR-0143: A float is written and compared as a double

**Status:** accepted · **Date:** 2026-11-11 · **Related:** PS-64, TexWright's request
`2026-11-09-kg-update-rewrites-unchanged-doubles.md`, DD-1a/DD-1b (`kg_read` / `kg_write` encoding), ER-1 (`Range`),
PS-54 (the shared filter grammar)

## Context

The store put a Python float into SQL as `repr(value)`, a bare decimal literal. ArcadeDB 26.7 reads a bare decimal
literal as a 32-bit FLOAT (probed live). A DOUBLE or DECIMAL column holding a value not exact in binary, such as
0.9523, therefore never equalled it: `kg_update`'s "only rows that differ" test was always true, so a derive step
rewrote 50 of 50 rows on every pass; equality, membership and `Range(ge=v, le=v)` filters on that column found 0 of
50; and a float on a property the pack does not declare, or on an edge, was stored at 32 bits (123456789.12345679
read back as 123456790.0). No pack in the engine, RuleWright or TexWright declares a FLOAT column; 54 declare DOUBLE.

## Decision

- A float is written and compared as a double literal, `repr(value) + "d"`, which ArcadeDB keeps at 64 bits (probed
  for exponents, negatives, subnormals and 17 significant digits). Only a field the pack declares `FLOAT` gets the
  bare literal, since a 32-bit column does not equal a double either.
- The filter grammar takes the queried type's declared property types, so a filter encodes each value as its
  column is declared: `kg_read`, `kg_count`, `kg_delete`, `kg_update` and `kg_edges`' start node.
- On a field the pack does not declare, a float equality or membership filter matches either width,
  `(f = Xd OR f = X)`, so values stored at 32 bits before this change are still found. `kg_update` on such a field
  rewrites an old 32-bit value once, as a double, and then settles.

## Consequences

- An unchanged measurement is a no-op for `kg_update`; filters on a DOUBLE find their values; new undeclared floats
  keep full precision. Live, the 50-row derive step changes 50 then 0 rows (50 then 50 before).
- Edge-property and target filters in `kg_edges` have no declared types, so they use the either-width form.
- Out of scope and unchanged: the hybrid-search chunk filter (`hybrid_search(filters=)`, its own literal encoder)
  and the vector arrays (`ARRAY_OF_FLOATS`, 32-bit by declaration).
