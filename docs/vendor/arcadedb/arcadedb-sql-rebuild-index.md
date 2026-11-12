<!-- Source: https://docs.arcadedb.com/arcadedb/reference/sql/sql-indexes (ArcadeDB docs, the REBUILD INDEX section, fetched 2026-11-12 for framework grounding; PS-67) -->

## SQL - `REBUILD INDEX`

Rebuilds automatic indexes.

**Syntax**
    
    
    REBUILD INDEX <index-name>

  * **`<index-name>`** It is the index name that you want to rebuild. Use `*` to rebuild all automatic indexes. Quote the index name if it contains special characters like square brackets.




__ |  During the rebuild, any idempotent queries made against the index, skip the index and perform sequential scans. This means that queries run slower during this operation. Non-idempotent commands, such as [`INSERT`](sql-insert#sql-insert-command), [`UPDATE`](sql-update#sql-update), and [`DELETE`](sql-delete#sql-delete) are blocked waiting until the indexes are rebuilt.   
---|---  
  
__ |  Since 26.11.1 `LSM_TREE`, `FULL_TEXT`, `GEOSPATIAL` and `HASH` indexes are rebuilt in place, one bucket at a time, without blocking the database: queries on every type keep running (they still use the old index until each bucket is replaced) and writes wait only while their own bucket is rebuilt. The index keeps its name. If the server stops mid-rebuild, every bucket reopens with its old or its new index, never a partial one. `CHECK DATABASE …​ FIX` repairs these index kinds the same way. Vector and sparse-vector indexes, HA databases, and a rebuild that changes what a full-text index stores (its similarity was altered) still use the blocking rebuild described above.   
---|---  
  
__ |  Since 26.11.1 an index missing the part of one of its type's buckets (for example after a rebuild interrupted by an older version) is reported with `valid = false`, queries do not use it and `COMPACT INDEX` refuses it. The server logs which buckets are missing at startup; `REBUILD INDEX <index-name>` fills them without blocking the database. Index files left behind by an interrupted rebuild are deleted at the next startup, with one warning each. Since 26.12.1 no query planner uses such an index (SQL, Cypher, the Java `select()` API, the MongoDB wire protocol): the query falls back to a scan and answers every row, where some of them used to return short results from it. `database.lookupByKey()` on it raises an `IllegalArgumentException` naming the index and asking for `REBUILD INDEX`, and Cypher [`SHOW INDEXES`](../cypher/cypher-clauses#cypher-show-indexes) reports it with state `FAILED` (issue #11303).   
---|---  
  
__ |  An index declared on a super type also covers the buckets of its sub types. `REBUILD INDEX *` (and `CHECK DATABASE …​ FIX`) rebuilds the part on a sub type's bucket under the index the super type declares. Since 26.11.1; before, the rebuild could detach the super type's own buckets from the index.   
---|---  
  
__ |  During normal operations an index rebuild is not necessary. Rebuild an index only if it breaks.   
---|---  
  
__ |  If the rebuild fails (for example because a stored record has a null key and the index null strategy is `ERROR`), the statement reports the error and the index is put back as it was, with its unique constraint. If the original null strategy cannot be applied to the stored data, the index is restored with `SKIP` and the original strategy is set in memory only, so after a restart it uses `SKIP` again. Since 26.10.1.   
---|---  
  
__ |  To build a large `LSM_TREE` index (unique or non-unique) over data that is already loaded (single-server, no active transaction), the Java API offers a much faster offline **sorted bulk build** that constructs the index directly in compacted form. See [Sorted bulk build](../../concepts/indexes#index-sorted-bulk-build).   
---|---  
  
**Examples**

  * Rebuild an index on the `email` property on the type `Profile`:



    
    
    ArcadeDB> REBUILD INDEX `Profile[email]`

  * Rebuild all indexes:



    
    
    ArcadeDB> REBUILD INDEX *

For more information, see:

  * `CREATE INDEX`

  * `COMPACT INDEX`

  * `DROP INDEX`

  * [Indexes](../../concepts/indexes#indexes)




