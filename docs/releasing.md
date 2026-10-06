# Releasing the engine

Engine changes land continuously (via PRs), but **releases are batched**: nothing is published per PR. A release
is cut deliberately, when a set of merged changes is ready, and published to PyPI automatically. Products such as
TexWright pick up the new release through their own automated dependency PRs (see "How products consume a
release" below). ADR-0123.

## The pipeline

```
merged PRs (conventional titles: feat:/fix:/...)
   └─► release-please.yml keeps ONE open "release PR" (bumps pyproject version + CHANGELOG.md)
          └─► you merge the release PR when the batch is ready
                 └─► release-please tags X.Y.Z + creates the GitHub Release
                        └─► publish.yml: tag == pyproject version guard → uv build → PyPI (OIDC)
```

## Conventional commits drive versioning

release-please reads the **squash-merged PR titles / commit messages** on `main` since the last release:

| prefix | effect (pre-1.0, `bump-minor-pre-major`) |
|---|---|
| `fix:` | patch bump (0.1.0 → 0.1.1), listed under Bug Fixes |
| `feat:` | minor bump (0.1.x → 0.2.0), listed under Features |
| `feat!:` / `BREAKING CHANGE:` footer | minor bump while 0.x (major once ≥ 1.0) |
| `perf:` `deps:` `revert:` | patch bump, listed under their own section |
| `docs:` `ci:` `chore:` `test:` `refactor:` `build:` `style:` | no release on their own (not user-facing) |

The visible/hidden split is set explicitly by `changelog-sections` in `release-please-config.json`. Keep it there:
the Python strategy's built-in default treats `docs:` as user-facing, so without that list a docs-only change opens a
release PR.

Use a scope for traceability, e.g. `fix(ingest): carry clause span_id (ADR-0122)`. A PR whose title is not
conventional is ignored for versioning, so it will not trigger or appear in a release.

## One-time setup

1. **`RELEASE_PLEASE_TOKEN` secret.** Create a fine-grained personal access token scoped to this repository with
   **Contents: read/write** and **Pull requests: read/write**, and add it under
   **Settings → Secrets and variables → Actions → New repository secret**. It is needed because a Release created
   with the default `GITHUB_TOKEN` does not trigger other workflows, so `publish.yml` would not fire. Without the
   secret, release-please still opens release PRs and creates releases (enable **Settings → Actions → General →
   "Allow GitHub Actions to create and approve pull requests"**), but you then publish by running `publish.yml`
   manually from the Actions tab with the release **tag** selected as the ref.
2. **`pypi` environment tag rule** must allow plain `X.Y.Z` tags (e.g. `*.*.*`); release-please tags without a `v`.
3. PyPI trusted publisher stays as is (owner `fzaidi-dai`, repo `RAG_Wright`, workflow `publish.yml`,
   environment `pypi`).

## Cutting a release

1. Merge fixes/features to `main` as usual (conventional titles).
2. Review the open **"chore: release X.Y.Z"** PR that release-please maintains; it shows the version bump and the
   generated changelog. Edit the changelog in that PR if you want.
3. Merge it when the batch is ready. The tag, GitHub Release, and PyPI publish follow automatically (approve the
   run if the `pypi` environment requires reviewers).

To force a specific version, add a commit with the footer `Release-As: X.Y.Z`.

## How products consume a release

Products depend on `rag-wright>=<floor>` with the exact version pinned by their `uv.lock`. Dependabot (uv
ecosystem) in the product repo opens a PR bumping the lock when a new release appears on PyPI; the product's CI,
including its live engine-seam tests, gates the merge. To use an engine fix **before** it is released, a product
temporarily points `[tool.uv.sources]` at the merged commit
(`rag-wright = { git = "https://github.com/fzaidi-dai/RAG_Wright", rev = "<sha>" }`) and removes the override once
the release lands. The product-starter templates (`docs/templates/product-starter/`) ship this setup. The engine
never knows about its consumers (Product → Engine, one way).
