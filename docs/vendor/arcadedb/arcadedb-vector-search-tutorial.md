<!-- Source: https://docs.arcadedb.com/arcadedb/tutorials/vector-search-tutorial (ArcadeDB docs, fetched for framework grounding) -->

# Vector Search Tutorial

<div id="preamble">

<div class="sectionbody">

<div class="paragraph">

This tutorial walks you through building a semantic search system with ArcadeDB. You will create vector embeddings, index them, query by similarity, and combine vector search with graph traversal.

</div>

</div>

</div>

<div class="sect1">

## <a href="#what-you-will-build" class="anchor"></a>What You Will Build

<div class="sectionbody">

<div class="paragraph">

A product catalog with semantic search: given a query like "portable computing device", find the most relevant products by embedding similarity rather than keyword matching.

</div>

</div>

</div>

<div class="sect1">

## <a href="#prerequisites" class="anchor"></a>Prerequisites

<div class="sectionbody">

<div class="ulist">

- ArcadeDB running (<a href="../how-to/operations/docker.html#quick-start-docker" class="xref page">Docker</a> or binary install)

- A way to send queries (<a href="../tools/console.html#console-tutorial" class="xref page">Console</a>, <a href="../reference/http-api/http.html#http-json-api" class="xref page">HTTP API</a>, or a <a href="python-quickstart.html#python-quickstart" class="xref page">Python</a>/<a href="javascript-quickstart.html#javascript-quickstart" class="xref page">JavaScript</a> client)

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-1-create-the-schema" class="anchor"></a>Step 1: Create the Schema

<div class="sectionbody">

<div class="paragraph">

Create a vertex type with a vector property:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE VERTEX TYPE Product
CREATE PROPERTY Product.name STRING
CREATE PROPERTY Product.category STRING
CREATE PROPERTY Product.embedding LIST OF FLOAT
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-2-create-a-vector-index" class="anchor"></a>Step 2: Create a Vector Index

<div class="sectionbody">

<div class="paragraph">

Create an LSM_VECTOR index on the embedding property. Specify the number of dimensions and the similarity metric:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE INDEX ON Product (embedding) LSM_VECTOR METADATA {
  dimensions: 4,
  similarity: 'COSINE'
}
```

</div>

</div>

<div class="admonitionblock note">

|  |  |
|----|----|
|  | In production, embeddings are typically 384-1536 dimensions. This tutorial uses 4 dimensions for simplicity. For production workloads, add `quantization: 'INT8'` for significantly better search performance — see <a href="../concepts/vector-search.html#quantization-performance" class="xref page">concepts/vector-search.adoc#quantization-performance</a> below. |

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-3-insert-data-with-embeddings" class="anchor"></a>Step 3: Insert Data with Embeddings

<div class="sectionbody">

<div class="paragraph">

Insert products with pre-computed embedding vectors:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE VERTEX Product SET name = 'Laptop',       category = 'Electronics', embedding = [0.9, 0.1, 0.8, 0.2]
CREATE VERTEX Product SET name = 'Tablet',        category = 'Electronics', embedding = [0.85, 0.15, 0.75, 0.25]
CREATE VERTEX Product SET name = 'Smartphone',    category = 'Electronics', embedding = [0.8, 0.2, 0.7, 0.3]
CREATE VERTEX Product SET name = 'Headphones',    category = 'Electronics', embedding = [0.6, 0.4, 0.5, 0.5]
CREATE VERTEX Product SET name = 'Novel',         category = 'Books',       embedding = [0.1, 0.9, 0.2, 0.8]
CREATE VERTEX Product SET name = 'Textbook',      category = 'Books',       embedding = [0.2, 0.8, 0.3, 0.7]
CREATE VERTEX Product SET name = 'Running Shoes', category = 'Sports',      embedding = [0.3, 0.5, 0.9, 0.1]
CREATE VERTEX Product SET name = 'Yoga Mat',      category = 'Sports',      embedding = [0.25, 0.55, 0.85, 0.15]
```

</div>

</div>

<div class="admonitionblock tip">

|  |  |
|----|----|
|  | In a real application, you would generate embeddings using an external model such as OpenAI’s `text-embedding-3-small` (1536 dimensions) or Sentence Transformers' `all-MiniLM-L6-v2` (384 dimensions). |

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-4-query-by-similarity" class="anchor"></a>Step 4: Query by Similarity

<div class="sectionbody">

<div class="paragraph">

Find the 3 products most similar to a query vector:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
SELECT name, category, distance FROM (
  SELECT expand(vector.neighbors('Product[embedding]', [0.88, 0.12, 0.78, 0.22], 3))
)
```

</div>

</div>

<div class="paragraph">

The `vector.neighbors()` function returns a list of results — `expand()` flattens it into individual rows so you can access properties like `name`, `category`, and `distance` directly. The query vector `[0.88, 0.12, 0.78, 0.22]` is close to the electronics cluster. The results should return Laptop, Tablet, and Smartphone — the three most similar items by cosine similarity.

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-5-add-graph-relationships" class="anchor"></a>Step 5: Add Graph Relationships

<div class="sectionbody">

<div class="paragraph">

Make the example more interesting by adding edges between products:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE EDGE TYPE FREQUENTLY_BOUGHT_WITH
CREATE EDGE TYPE SIMILAR_TO

CREATE EDGE FREQUENTLY_BOUGHT_WITH
  FROM (SELECT FROM Product WHERE name = 'Laptop')
  TO (SELECT FROM Product WHERE name = 'Headphones')

CREATE EDGE SIMILAR_TO
  FROM (SELECT FROM Product WHERE name = 'Laptop')
  TO (SELECT FROM Product WHERE name = 'Tablet')

CREATE EDGE FREQUENTLY_BOUGHT_WITH
  FROM (SELECT FROM Product WHERE name = 'Novel')
  TO (SELECT FROM Product WHERE name = 'Textbook')
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-6-combine-vector-search-with-graph-traversal" class="anchor"></a>Step 6: Combine Vector Search with Graph Traversal

<div class="sectionbody">

<div class="paragraph">

Find similar products, then expand recommendations through graph relationships:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Step 1: Find top 3 by vector similarity
SELECT name, category, distance FROM (
  SELECT expand(vector.neighbors('Product[embedding]', [0.88, 0.12, 0.78, 0.22], 3))
)
```

</div>

</div>

<div class="paragraph">

Then traverse from those results to find related products:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Step 2: Get products frequently bought with the top match
SELECT friend.name, friend.category
FROM MATCH {type: Product, as: product, where: (name = 'Laptop')}
     -FREQUENTLY_BOUGHT_WITH-> {type: Product, as: friend}
```

</div>

</div>

<div class="paragraph">

This two-step pattern — vector search to find semantically similar items, then graph traversal to expand through relationships — is the foundation of the <a href="../use-cases/graph-rag.html#use-case-graph-rag" class="xref page">Graph RAG</a> and <a href="../use-cases/recommendation-engine.html#use-case-recommendation-engine" class="xref page">Recommendation Engine</a> patterns.

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-7-use-different-similarity-metrics" class="anchor"></a>Step 7: Use Different Similarity Metrics

<div class="sectionbody">

<div class="paragraph">

Create additional indexes with different metrics for comparison:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Euclidean distance (absolute distance in vector space)
CREATE PROPERTY Product.embedding_l2 LIST OF FLOAT
CREATE INDEX ON Product (embedding_l2) LSM_VECTOR METADATA {
  dimensions: 4,
  similarity: 'EUCLIDEAN'
}

-- Dot product (fastest for normalized vectors)
CREATE PROPERTY Product.embedding_dot LIST OF FLOAT
CREATE INDEX ON Product (embedding_dot) LSM_VECTOR METADATA {
  dimensions: 4,
  similarity: 'DOT_PRODUCT'
}
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-8-control-search-quality-with-efsearch" class="anchor"></a>Step 8: Control Search Quality with efSearch

<div class="sectionbody">

<div class="paragraph">

By default, ArcadeDB uses an adaptive search strategy that works well for most queries. You can override it per-query by passing efSearch as the 4th argument:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
-- Higher efSearch for better recall (useful for critical queries)
SELECT name, category, distance FROM (
  SELECT expand(vector.neighbors('Product[embedding]', [0.88, 0.12, 0.78, 0.22], 3, 200))
)
```

</div>

</div>

<div class="paragraph">

See <a href="../concepts/vector-search.html#adaptive-efsearch" class="xref page">Adaptive efSearch</a> for details on how the default strategy works.

</div>

</div>

</div>

<div class="sect1">

## <a href="#step-9-enable-quantization-for-large-datasets" class="anchor"></a>Step 9: Enable Quantization for Large Datasets

<div class="sectionbody">

<div class="paragraph">

For production datasets with many vectors, enable INT8 quantization to reduce memory by 75%:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
CREATE VERTEX TYPE LargeProduct
CREATE PROPERTY LargeProduct.embedding ARRAY_OF_FLOATS

CREATE INDEX ON LargeProduct (embedding) LSM_VECTOR METADATA {
  dimensions: 384,
  similarity: 'COSINE',
  quantization: 'INT8'
}
```

</div>

</div>

<div class="paragraph">

Queries work exactly the same — quantization is transparent:

</div>

<div class="listingblock">

<div class="content">

``` highlightjs
SELECT name, distance FROM (
  SELECT expand(vector.neighbors('LargeProduct[embedding]', $queryVector, 10))
)
```

</div>

</div>

</div>

</div>

<div class="sect1">

## <a href="#next-steps" class="anchor"></a>Next Steps

<div class="sectionbody">

<div class="ulist">

- <a href="../concepts/vector-search.html#vector-search-concepts" class="xref page">Vector Search Concepts</a> — Architecture, algorithms, and parameter tuning

- <a href="../how-to/data-modeling/vector-embeddings.html#vector-embeddings-howto" class="xref page">Vector Embeddings How-To</a> — Production best practices

- <a href="../use-cases/recommendation-engine.html#use-case-recommendation-engine" class="xref page">Recommendation Engine</a> — Full use case with vector + graph + time-series

- <a href="../use-cases/graph-rag.html#use-case-graph-rag" class="xref page">Graph RAG</a> — Vector + graph for LLM retrieval augmentation

</div>

</div>

</div>

<span class="prev">[Quick Start with Docker](../how-to/operations/docker.html)</span> <span class="next">[Time Series Tutorial](time-series-tutorial.html)</span>
