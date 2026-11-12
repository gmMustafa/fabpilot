-- Runs once, on first container start with an empty data volume, against $POSTGRES_DB.
-- Extensions are per-database: being installed in the image is not enough, each DB must CREATE them.

-- TimescaleDB: hypertables for sensor_readings (time-partitioned chunks, compression, time_bucket()).
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- pgvector: adds the vector(n) column type, the distance operators
-- (<=> cosine, <-> L2, <#> negative inner product) and HNSW/IVFFlat indexes for RAG embeddings.
CREATE EXTENSION IF NOT EXISTS vector;

-- Print the versions of both extensions in the init log, so we can see they loaded.
SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('timescaledb', 'vector')
ORDER BY extname;