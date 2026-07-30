<!-- Source: https://docs.arcadedb.com/arcadedb/reference/sql/sql-buckets.html (ArcadeDB docs, fetched for framework grounding) -->
<!-- Source: https://docs.arcadedb.com/arcadedb/reference/sql/sql-types.html -->
<!-- Source: https://docs.arcadedb.com/arcadedb/reference/sql/sql-select.html -->

# Buckets and Schema (physical storage partitioning inside one database)

This grounds ArcadeDB's **bucket** concept for the framework index. A bucket is an ArcadeDB-internal
storage construct and has nothing to do with cloud object-storage (S3/GCS) buckets.

## What a bucket is

A **bucket** is a **physical storage partition inside a single ArcadeDB database** (the concept inherits
from OrientDB "clusters"). Records of a type are physically stored in that type's bucket(s). Buckets are an
*intra-database* mechanism: they partition data physically within one store; they are NOT separate databases
or separate stores, so using multiple buckets does not create a cross-store consistency problem.

## Type-to-bucket mapping

- A **type** (class; vertex/edge/document) is backed by **one or more buckets**.
- The default number of buckets created for a new type is **1**, taken from the global configuration
  `arcadedb.typeDefaultBuckets` (`TYPE_DEFAULT_BUCKETS`), which defaults to `1`.
- A type's records are distributed across its buckets by a **bucket selection strategy**:
  - **`round-robin`** — the database-level default; inserts spread across the type's buckets in turn.
  - **`partitioned('<property>')`** — place each record in a bucket chosen by a property value (data locality
    / partitioning by key, e.g. a tenant id).

## DDL — buckets

```sql
CREATE BUCKET <bucket> [ID <bucket-id>]     -- first char must be a letter; then alphanumerics, _ and -
DROP BUCKET <bucket-name>|<bucket-id>
TRUNCATE BUCKET <bucket>                     -- deletes ALL records of a bucket; lower level than DELETE
```

## DDL — types with buckets

```sql
-- create a type backed by N buckets (improves insert concurrency on multiple cores)
CREATE VERTEX TYPE Car BUCKETS 4

-- create a type bound to specific, named buckets
CREATE VERTEX TYPE Car BUCKET Car_classic, Car_modern

-- add a bucket to an existing type
ALTER TYPE Account BUCKET +account2

-- change how records are placed across the type's buckets
ALTER TYPE Account BucketSelectionStrategy `partitioned('id')`
```

If you `CREATE BUCKET` standalone and want it attached to a type, follow it with `ALTER TYPE ... BUCKET +...`.

## Querying a specific bucket

The `FROM` target of a `SELECT` can be a type, a bucket, RIDs, an index, or a schema view:

```sql
SELECT ... FROM Person                 -- all buckets of the Person type
SELECT ... FROM BUCKET:person          -- only records in the bucket named 'person'
SELECT ... FROM BUCKET:12              -- only records in bucket id 12
SELECT ... FROM [#10:3, #10:4]          -- specific RIDs
SELECT ... FROM schema:buckets          -- the schema view listing buckets
```

Targeting `BUCKET:<name>` executes the query only over that bucket's records instead of the whole type.

## Why buckets exist (use cases)

- **Insert concurrency / parallelism.** "When working with multiple cores, it is recommended that you use
  multiple buckets to improve concurrency during inserts." Multiple buckets let the engine parallelize writes.
- **Physical partitioning and locality.** `partitioned('<property>')` places records by key (e.g. per tenant),
  giving physical locality within one database.
- **Scoped scans.** `FROM BUCKET:<name>` reads a single partition without a full type scan.

## Storage/deployment implication (grounding note)

Buckets are backed by ArcadeDB's on-disk page files; ArcadeDB is a transactional engine that persists to a
**filesystem / block storage**, not to object storage. In a cloud deployment the live database (and its
buckets) sits on a persistent block volume (e.g. a GKE PersistentDisk); object storage (S3/GCS) is for source
documents, caches, and database **backups/snapshots**, not for hosting the live store. Partitioning KG record
types vs. vector-index-bearing types (or per-tenant partitioning) is done with **buckets inside the one
ArcadeDB store**, which keeps the single-store invariant (one hybrid-index-plus-KG store, no cross-store join).
