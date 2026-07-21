-- doc03 STORE — the §3.3 dual-storage Postgres plane (Plane 1: live append).
-- Byte-for-byte the sealed spec DDL (03-MEETING-UNDERSTANDING.md §3.3). The GCS
-- artifact (Plane 2) is object-versioned in the bucket, not a table.
--
-- Apply: psql "$DOC03_STORE_SPEC_DB" -f libs/db/migrations/doc03_notes_store.sql
-- (idempotent: IF NOT EXISTS so a re-run is a no-op).

CREATE TABLE IF NOT EXISTS transcript_segments (
  id          bigserial PRIMARY KEY,
  meeting_id  uuid NOT NULL,
  speaker     text,
  text        text NOT NULL,
  start_s     double precision,
  end_s       double precision,
  status      text NOT NULL DEFAULT 'pending',   -- 'pending' | 'comprehended' | 'gap'
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS note_deltas (          -- the append-only ledger; the live object is its left-fold
  id             bigserial PRIMARY KEY,
  meeting_id     uuid NOT NULL,
  entry_id       text NOT NULL,                   -- stable across every patch of the same entry
  op             text NOT NULL,                   -- 'add' | 'patch' | 'close'
  payload        jsonb NOT NULL,
  window_start_s double precision,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS note_deltas_meeting_id_id_idx ON note_deltas (meeting_id, id);
CREATE INDEX IF NOT EXISTS transcript_segments_meeting_id_id_idx ON transcript_segments (meeting_id, id);
-- Replay idempotency (§3.3 / CANONICAL §12.10): a stray re-append is a silent no-op.
CREATE UNIQUE INDEX IF NOT EXISTS note_deltas_source_window_uniq
  ON note_deltas (meeting_id, window_start_s, entry_id, op);
