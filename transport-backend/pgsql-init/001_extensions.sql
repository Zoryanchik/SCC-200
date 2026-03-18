-- Ensure required extensions exist.
-- This runs only on first init of the Postgres data directory.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
