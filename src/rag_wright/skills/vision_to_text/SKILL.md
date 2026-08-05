---
name: vision_to_text
description: >
  The scanned-image transcription method: given a single page image from an image-only filing, transcribe all
  visible text exactly, preserving reading order, and output only the transcribed text. A single grounded
  vision-language act on the GENERAL model (Gemma 4 class) through the model seam; the ingestion-side twin of
  answer generation (both are the FR-C.9 generation capability, ADR-0014). Applied by the vision_to_text skill
  runtime at ingestion for the image-only PDF subset.
---

# Vision-to-text: transcribe the text visible in this image

This skill teaches a **method** for turning a page image into its text, so an image-only filing can be chunked,
embedded, and reasoned over like any parsed document. It is a single vision-language reading, not a workflow.

## The method

- **Transcribe all text visible in the image exactly.** Reproduce the characters as written -- do not
  paraphrase, summarize, correct, translate, or complete anything.
- **Preserve reading order.** Follow the page's natural top-to-bottom, left-to-right flow (and column order
  where the page is multi-column), so the transcription reads as the document reads.
- **Output only the transcribed text.** No commentary, no description of the layout, no headings you invent,
  no "here is the text" preamble -- just the text itself.

## Boundaries

- If a region is unreadable, transcribe what is legible and do not fabricate the rest.
- The transcription is downstream evidence: it must be faithful to the page, because everything built on top
  of it (chunks, embeddings, extracted facts, citations) inherits its errors.

## What this skill does NOT own (the applying capability's job)

- the **model choice and the seam** (the GENERAL role via the model-profile seam -- product = self-hosted
  Gemma-class, ADR-0039) and the **multimodal message assembly** (base64 data URI) are the capability runtime's
  plumbing, not the method;
- the **output contract** (`VisionTranscription`) is attached by the capability.
