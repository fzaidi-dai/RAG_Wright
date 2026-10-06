# ADR-0123: Batched engine releases via release-please; products consume releases via Dependabot

- Status: Accepted
- Date: 2026-10-07

## Context

The engine is published to PyPI (`rag-wright`) by `publish.yml` on a GitHub Release (OIDC trusted publishing).
Products (TexWright first) will surface engine bugs that get fixed via engine PRs, but publishing per PR is
undesirable: releases should batch several merged PRs. Products also need an automated way to adopt new releases,
and occasionally an unreleased fix, without the engine knowing about them (Product → Engine, ADR-0052).

## Decision

1. **Engine: release-please** (`.github/workflows/release-please.yml`, manifest config) keeps one open release PR
   accumulating conventional commits since the last tag, bumping `pyproject.toml` + `CHANGELOG.md`. Merging it
   tags plain `X.Y.Z` and creates the GitHub Release, which fires the existing `publish.yml`. `bump-minor-pre-major`
   keeps breaking changes on 0.x as minor bumps.
2. **A PAT (`RELEASE_PLEASE_TOKEN`) creates the Release**, because Releases created with `GITHUB_TOKEN` do not
   trigger other workflows. Publish is NOT chained as a reusable workflow, because PyPI trusted publishing trusts
   the top-level `publish.yml`, and keeping publish on the tag ref preserves the `pypi` environment's tag rule.
3. **Commit/PR titles on `main` follow Conventional Commits** (`fix:`/`feat:`/...), since they drive versioning.
4. **Products consume releases with Dependabot (uv ecosystem)**: `>=` floor in `pyproject`, exact version in
   `uv.lock`, a bot PR per release gated by the product's CI (incl. live engine-seam tests). An unreleased fix is
   taken temporarily via a `[tool.uv.sources]` git `rev` pin, removed when the release lands.

## Consequences

- Releases are a deliberate merge of the release PR; no manual version/tag/changelog editing, and the
  tag-equals-version guard holds by construction.
- One-time setup: the PAT secret, and the `pypi` environment's tag rule must allow plain `X.Y.Z`.
- Task-id commit subjects (e.g. `PREP-x: ...`) must now carry a conventional prefix (`fix(PREP-x): ...`) to count.
- The engine stays unaware of consumers; each product's automation lives in its own repo (product-starter template).
