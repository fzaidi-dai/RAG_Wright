<!-- Source: https://docs.arcadedb.com/arcadedb/how-to/data-modeling/vector-embeddings (ArcadeDB docs, fetched for framework grounding) -->

# Vector Embeddings

<div id="preamble">

<div class="sectionbody">

<div class="paragraph">

This guide covers practical decisions for working with vector embeddings in ArcadeDB: choosing dimensions, creating indexes, tuning parameters, and combining vector search with other query types.

</div>

</div>

</div>

<div class="sect1">

## <a href="#choosing-an-embedding-model" class="anchor"></a>Choosing an Embedding Model

<div class="sectionbody">

<div class="paragraph">

Your embedding model determines the `dimensions` parameter for the index:

</div>

| Model | Dimensions | Notes |
|----|----|----|
| OpenAI `text-embedding-3-small` | 1536 | General purpose, high quality |
| OpenAI `text-embedding-3-large` | 3072 | Highest quality, largest memory footprint |
| Sentence Transformers `all-MiniLM-L6-v2` | 384 | Fast, open source, good quality |
| Sentence Transformers `all-mpnet-base-v2` | 768 | Better quality, slower |
| Cohere `embed-english-v3.0` | 1024 | Good balance of quality and size |
| CLIP (image + text) | 512 | Multi-modal image/text |

<div class="admonitionblock tip">

|  |  |
|----|----|
|  | Start with 384 dimensions (MiniLM) for prototyping. Move to 768+ for production quality. Use quantization to manage memory at higher dimensions. |

</div>

</div>

</div>

<div class="sect1">

## <a href="#creating-a-vector-index" class="anchor"></a>Creating a Vector Index

<div class="sectionbody">

<div class="paragraph">

Recommended index creation with INT8 quantization:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE VERTEX TYPE Document
CREATE PROPERTY Document.content STRING
CREATE PROPERTY Document.embedding LIST OF FLOAT

CREATE INDEX ON Document (embedding) LSM_VECTOR METADATA {
  dimensions: 384,
  similarity: 'COSINE',
  quantization: 'INT8'
}
```

</div>

</div>

<div class="paragraph">

INT8 quantization is recommended for all production workloads. It provides 2.5x faster search and 4x lower memory usage with negligible accuracy loss (see <a href="../../concepts/vector-search.html#quantization-performance" class="xref page">concepts/vector-search.adoc#quantization-performance</a>). Only omit quantization for very small datasets (\< 10K vectors) where maximum precision matters.

</div>

<div class="paragraph">

Production-ready index with additional tuning:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE INDEX ON Document (embedding) LSM_VECTOR METADATA {
  dimensions: 384,
  similarity: 'COSINE',
  quantization: 'INT8',
  maxConnections: 16,
  beamWidth: 100
}
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#choosing-a-similarity-function" class="anchor"></a>Choosing a Similarity Function

<div class="sectionbody">

| Function | Choose When | Avoid When |
|----|----|----|
| **COSINE** | Using text embedding models (most common). Vectors may have varying magnitudes. | Vectors represent absolute quantities (distances, counts). |
| **DOT_PRODUCT** | Vectors are already L2-normalized. You need maximum query speed. | Vectors are not normalized (results will be incorrect). |
| **EUCLIDEAN** | Working with spatial data, sensor readings, or continuous measurements. | Comparing text embeddings of different lengths. |

</div>

</div>

<div class="sect1">

## <a href="#quantization-trade-offs" class="anchor"></a>Quantization Trade-offs

<div class="sectionbody">

<div class="paragraph">

Use INT8 quantization for most use cases. It provides 4x memory savings with minimal accuracy loss and significantly faster ingestion and search:

</div>

<div class="ulist">

- **\< 10K vectors**: `NONE` is fine, but `INT8` works well too

- **10K - 1M vectors**: Use `INT8` (4x memory savings, \< 2% accuracy loss) — **recommended**

- **\> 1M vectors**: Use `INT8` for general use, or `PRODUCT` for zero-disk-I/O graph construction on very large datasets

- **Extreme compression**: Use `BINARY` for first-pass filtering, then rerank with full vectors

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- INT8: recommended for most workloads
CREATE INDEX ON Doc (embedding) LSM_VECTOR METADATA {
  dimensions: 768,
  similarity: 'COSINE',
  quantization: 'INT8'
}

-- PRODUCT: for very large datasets, enables in-memory graph build
CREATE INDEX ON Doc (embedding) LSM_VECTOR METADATA {
  dimensions: 1024,
  similarity: 'COSINE',
  quantization: 'PRODUCT'
}
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#vector-int8-ingest" class="anchor"></a>INT8 Pre-Quantized Ingest

<div class="sectionbody">

<div class="admonitionblock note">

|  |  |
|----|----|
|  | Available since ArcadeDB v26.5.1. See <a href="../../concepts/vector-search.html#vector-encoding" class="xref page">Vector Encoding</a> for the underlying concept. |

</div>

<div class="paragraph">

When your embedding provider already emits signed-int8 vectors (Cohere `int8` endpoints, OpenAI `text-embedding-3-large` reduced precision, Sentence Transformers with int8 quantization), use `encoding: 'INT8'` to keep the bytes byte-shaped end-to-end:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE PROPERTY Doc.embedding BINARY;
CREATE INDEX ON Doc (embedding) LSM_VECTOR METADATA {
  "dimensions": 1024,
  "similarity": "COSINE",
  "encoding": "INT8"
};
```

</div>

</div>

<div class="paragraph">

What this saves:

</div>

<div class="ulist">

- HTTP payload: 4x smaller (1 byte/dim vs 4 bytes/dim) when sending vectors with the typed-marker convention — see <a href="../../reference/http-api/http.html#vector-http-typed-markers" class="xref page">HTTP wire convention</a>.

- Document bucket storage: 4x smaller, since the property column is `BINARY` instead of `ARRAY_OF_FLOATS`.

- No client-side `int8 → float32 → server` round trip; the precision the provider already discarded does not get padded back out for the wire.

</div>

<div class="paragraph">

What this does **not** change today:

</div>

<div class="ulist">

- HNSW graph and search internally still run on `float32` (JVector 4.0.0-rc.8 contract). The engine dequantizes once on the read path. Native int8 HNSW is tracked upstream at [datastax/jvector#665](https://github.com/datastax/jvector/issues/665).

- `encoding` is independent of `quantization`. Combining `encoding: 'INT8'` with `quantization: 'INT8'` is rejected at index creation — pick one, not both.

</div>

<div class="paragraph">

When **not** to use INT8 encoding:

</div>

<div class="ulist">

- Your provider emits `float32` (or `float64`) and you do not have an int8 quantizer client-side. The default `FLOAT32` encoding skips the dequantize hop on every search and keeps full precision in the documents.

- You want the index-internal compression benefit (search-time memory footprint). That is `quantization: 'INT8'`, not `encoding: 'INT8'`. They are orthogonal.

</div>

</div>

</div>

<div class="sect1">

## <a href="#tuning-for-recall-vs-speed" class="anchor"></a>Tuning for Recall vs Speed

<div class="sectionbody">

<div class="paragraph">

Adjust `maxConnections` and `beamWidth` based on your priorities:

</div>

| Profile | maxConnections | beamWidth | Trade-off |
|----|----|----|----|
| **Default** | 16 | 100 | Balanced for most workloads |
| **High recall** | 32 | 200 | Better accuracy, 2-3x slower builds, 50% more memory |
| **Fast indexing** | 12 | 80 | 2x faster builds, 5-10% lower recall |
| **Memory constrained** | 8 | 60 | Minimal memory footprint |

<div class="paragraph">

For datasets over 100K vectors or with 1024+ dimensions, enable hierarchical mode:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE INDEX ON Doc (embedding) LSM_VECTOR METADATA {
  dimensions: 1536,
  similarity: 'COSINE',
  quantization: 'INT8',
  addHierarchy: true,
  maxConnections: 32,
  beamWidth: 200
}
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#tuning-efsearch" class="anchor"></a>Tuning efSearch

<div class="sectionbody">

<div class="paragraph">

The `efSearch` parameter controls how many candidates the search explores at query time. By default, ArcadeDB uses an <a href="../../concepts/vector-search.html#adaptive-efsearch" class="xref page">adaptive strategy</a> that works well for most workloads. You only need to tune efSearch if you have specific recall or latency requirements.

</div>

| Profile | efSearch | Trade-off |
|----|----|----|
| **Adaptive** (default) | auto | Two-pass: fast first pass (`2×k`), wider retry (`10×k`) if needed |
| **High recall** | 200-500 | Consistent high accuracy, higher latency |
| **Low latency** | 20-50 | Fast responses, lower recall on hard queries |

<div class="paragraph">

You can override efSearch per-query without changing the index:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- High recall for a critical search
SELECT expand(vector.neighbors('Doc[embedding]', $queryVector, 10, 500))

-- Low latency for autocomplete/typeahead
SELECT expand(vector.neighbors('Doc[embedding]', $queryVector, 5, 30))
```

</div>

</div>

<div class="paragraph">

Or set a default on the index:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE INDEX ON Doc (embedding) LSM_VECTOR METADATA {
  dimensions: 768,
  similarity: 'COSINE',
  quantization: 'INT8',
  efSearch: 200
}
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#multi-modal-embeddings" class="anchor"></a>Multi-Modal Embeddings

<div class="sectionbody">

<div class="paragraph">

Store multiple embeddings per record for different search modalities:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE VERTEX TYPE Product
CREATE PROPERTY Product.imageEmbedding ARRAY_OF_FLOATS
CREATE PROPERTY Product.textEmbedding  ARRAY_OF_FLOATS

CREATE INDEX ON Product (imageEmbedding) LSM_VECTOR METADATA {dimensions: 512, similarity: 'COSINE'}
CREATE INDEX ON Product (textEmbedding)  LSM_VECTOR METADATA {dimensions: 768, similarity: 'COSINE'}
```

</div>

</div>

<div class="paragraph">

Query each index independently:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Search by image similarity
SELECT name, distance FROM (
  SELECT expand(vector.neighbors('Product[imageEmbedding]', $imageVector, 10))
)

-- Search by text similarity
SELECT name, distance FROM (
  SELECT expand(vector.neighbors('Product[textEmbedding]', $textVector, 10))
)
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#hybrid-search-dense-sparse-full-text" class="anchor"></a>Hybrid Search: Dense + Sparse + Full-Text

<div class="sectionbody">

<div class="admonitionblock note">

|  |  |
|----|----|
|  | The `vector.fuse` operator described below is available since ArcadeDB v26.5.1. Earlier versions required two queries plus client-side fusion via `vector.rrfScore`. |

</div>

<div class="paragraph">

Combine dense vector similarity with sparse retrieval and/or keyword matching in a single server-side query. `vector.fuse` accepts any number of ranked sub-pipelines plus a fusion strategy (`RRF`, `DBSF`, `LINEAR`) and returns one ranked top-K:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Schema: dense + sparse properties + indexes (sparse needs LSM_SPARSE_VECTOR).
CREATE PROPERTY Document.dense   ARRAY_OF_FLOATS;
CREATE PROPERTY Document.tokens  ARRAY_OF_INTEGERS;
CREATE PROPERTY Document.weights ARRAY_OF_FLOATS;

CREATE INDEX ON Document (dense) LSM_VECTOR
  METADATA { dimensions: 384, similarity: 'COSINE' }

CREATE INDEX ON Document (tokens, weights) LSM_SPARSE_VECTOR
  METADATA { dimensions: 30000, modifier: 'IDF' }

-- Hybrid retrieval in one statement.
SELECT expand(`vector.fuse`(
    `vector.neighbors`('Document[dense]', :denseVec, 50),
    `vector.sparseNeighbors`('Document[tokens,weights]', :qIdx, :qVal, 50),
    { fusion: 'RRF', groupBy: 'source_file', groupSize: 1 }
)) LIMIT 10
```

</div>

</div>

<div class="ulist">

- `vector.neighbors` exposes `distance` (lower = better); `vector.fuse` auto-flips it so dense and sparse sources compose without manual rescaling.

- `groupBy` + `groupSize` collapse same-source duplicates server-side. Drop the option to return chunk-level results.

- Pre-fusion grouping is also possible by attaching `{ groupBy: 'source_file', groupSize: 1 }` to each individual source.

</div>

<div class="paragraph">

To include full-text alongside dense/sparse, add a third source built from `SEARCH_INDEX`:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
SELECT expand(`vector.fuse`(
    `vector.neighbors`('Document[dense]', :denseVec, 100),
    `vector.sparseNeighbors`('Document[tokens,weights]', :qIdx, :qVal, 100),
    (SELECT @rid, $score FROM Document
     WHERE SEARCH_INDEX('Document[content]', 'machine learning') = true),
    { fusion: 'RRF' }
)) LIMIT 10
```

</div>

</div>

<div class="paragraph">

Pick the strategy that matches your scoring shape:

</div>

<div class="ulist">

- `RRF` — rank-only, indifferent to score scales. Default, safest with mixed source types.

- `DBSF` — mean +/- 3sigma normalisation per source then weighted sum. Use when scores are roughly Gaussian on each side.

- `LINEAR` — per-source min-max normalisation then weighted sum. Use with already-tuned offline weights.

</div>

<div class="paragraph">

For the legacy two-query workaround (still supported via `vector.rrfScore` and `vector.hybridScore` on already-computed scores), see the <a href="../../reference/sql/sql-functions.html#sql-functions" class="xref page">SQL Vector Functions</a> reference.

</div>

</div>

</div>

<div class="sect1">

## <a href="#batch-ingestion" class="anchor"></a>Batch Ingestion

<div class="sectionbody">

<div class="paragraph">

For bulk loading vectors, batch your inserts within transactions:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
BEGIN

CREATE VERTEX Document SET content = 'First document',  embedding = [0.1, 0.2, ...]
CREATE VERTEX Document SET content = 'Second document', embedding = [0.3, 0.4, ...]
-- ... more inserts ...

COMMIT
```

</div>

</div>

<div class="admonitionblock tip">

|  |  |
|----|----|
|  | For large bulk loads, increase `mutationsBeforeRebuild` to delay index rebuilds until after the load completes, then trigger a rebuild. |

</div>

<div class="admonitionblock note">

|  |  |
|----|----|
|  | When vectors are inserted below the rebuild threshold, an inactivity timer ensures the graph is still rebuilt after a period of no new mutations (default: 15 seconds). This prevents buffered vectors from remaining in the brute-force delta buffer indefinitely during low-volume ingestion. Configure via `inactivityRebuildTimeoutMs` (per-index metadata or `arcadedb.vectorIndex.inactivityRebuildTimeoutMs` globally). Set to 0 to disable. |

</div>

<div class="paragraph">

If you create the index *before* inserting data (e.g., during schema setup), set `buildGraphNow: false` to skip the initial (empty) graph build. The graph will be built lazily on the first search:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Schema setup phase: defer graph build since no data exists yet
CREATE INDEX ON Document (embedding) LSM_VECTOR METADATA {
  dimensions: 384,
  similarity: 'COSINE',
  quantization: 'INT8',
  buildGraphNow: false
}

-- Bulk load data...
-- Graph is built automatically on first vector.neighbors() query
```

</div>

</div>

<div class="paragraph">

If you create the index *after* data is already loaded, leave `buildGraphNow` at its default (`true`) so the index is immediately ready to query.

</div>

</div>

</div>

<div class="sect1">

## <a href="#global-configuration" class="anchor"></a>Global Configuration

<div class="sectionbody">

<div class="paragraph">

Set database-wide defaults for vector index parameters:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
ALTER DATABASE `arcadedb.vectorIndex.locationCacheSize` 100000
ALTER DATABASE `arcadedb.vectorIndex.graphBuildCacheSize` 10000
ALTER DATABASE `arcadedb.vectorIndex.mutationsBeforeRebuild` 100
ALTER DATABASE `arcadedb.vectorIndex.inactivityRebuildTimeoutMs` 15000
ALTER DATABASE `arcadedb.vectorIndex.storeVectorsInGraph` false
```

</div>

</div>

<div class="paragraph">

Per-index metadata overrides these global settings.

</div>

</div>

</div>

<div class="sect1">

## <a href="#further-reading" class="anchor"></a>Further Reading

<div class="sectionbody">

<div class="ulist">

- <a href="../../concepts/vector-search.html#vector-search-concepts" class="xref page">Vector Search Concepts</a> — Architecture and algorithm details

- <a href="../../tutorials/vector-search-tutorial.html#vector-search-tutorial" class="xref page">Vector Search Tutorial</a> — Step-by-step hands-on guide

- <a href="../../reference/java-api/java-vectors.html#java-vectors" class="xref page">Java Vector API</a> — Programmatic index management

- <a href="../../reference/sql/sql-functions.html#sql-functions" class="xref page">SQL Vector Functions</a> — Complete function reference

</div>

</div>

</div>

<span class="prev">[Materialized Views](materialized-views.html)</span> <span class="next">[Generic Importer](../migration/importer.html)</span>
