-- Enable the pgvector extension so we can store embedding vectors.
-- This runs once when the container is first created.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
