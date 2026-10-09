---
name: span_relevance_judgment
description: >
  The corpus-retrieval RELEVANCE method: given ONE retrieved span (a passage of a document) and ONE structured
  condition being searched for (a category, optionally a specific condition within it, with the user's question
  as context), decide whether the span ACTUALLY ADDRESSES that condition -- relevant, not_relevant, or uncertain
  from the text. It returns a VERDICT, not a score, so no caller has to choose a similarity threshold. The applying
  capability owns the deterministic guarantees (verdict vocabulary, conservative default) -- this skill teaches only
  the reading. A domain adds its own guidance (what its categories look like) below this method.
---

# Span relevance: does this retrieved span address this condition?

A retrieval leg returns the top-k nearest spans, so a nonsense query still comes back with a full page of results.
Retrieval ranks by similarity; it never asks whether a span is *about* the thing searched for. This skill supplies
that missing judgement, so a search can honestly say "nothing in this corpus addresses this condition" instead of
surfacing near-misses.

## The one hard constraint: you see only the span text

You are given the condition and ONE span's text. You judge whether **that span text** addresses the condition.
You cannot see the rest of the document or the corpus. Judge only what the span itself shows.

## What the condition is (and how to weigh its parts)

- **category** (primary) -- the kind of passage being searched for. This is the main test: is the span a passage
  of, or squarely about, this category?
- **specific condition** (when present) -- a narrower test within the category. When given, the span must address
  THIS, not just the general category.
- **the user's question** -- CONTEXT ONLY. In a multi-condition search one question is shared across several
  conditions, so it may be broader than, or only loosely tied to, this particular condition. Never treat a span as
  relevant just because it echoes a word from the question; anchor on the category and the specific condition.

## Typed properties are evidence, not proof

The span may arrive with typed properties already detected on it (`property = value`). These may have been
extracted from the question once and reused across every condition in the search, so a property being present
does **not** prove the span is about *this* condition: a passage of an unrelated category can carry the same
property value. Read the properties as a hint, then decide from the span text itself.

## The three verdicts

- **relevant** -- the span is clearly a passage of the condition's category, or squarely addresses the specific
  condition. A domain expert scanning the results would say "yes, this is what you were looking for."

- **not_relevant** -- the span is about something else. It was returned because it was among the nearest by
  similarity (or shares an incidental property), but it does not address this category / condition. This is the
  verdict that makes an honest "not found" reachable: when every returned span is not_relevant, the corpus does not
  contain the condition.

- **uncertain** -- the span text is too ambiguous, partial, or truncated to tell whether it addresses the
  condition. Reserve this for genuine ambiguity, not for "probably not" (that is not_relevant) and not for
  "probably yes" (that is relevant). Do not use uncertain to avoid a decision the text supports.

## Output

Return the verdict (relevant / not_relevant / uncertain), a one- or two-sentence rationale grounded in the span
text, and a confidence in [0, 1].

## What this skill does NOT own

The vocabulary mapping, the conservative default when you cannot be read, and how the verdicts roll up into a
product's matched / possible / not-found grouping are the APPLYING capability's and the caller's concern, not this
method's. This skill decides one span against one condition and explains why.
