# CLAUDE.md — Video Claim Verification MVP

## Project overview

Local prototype for a production-scale video-based delivery claim verification system. Customers upload short videos of damaged products through a chat interface. The system chunks the video, stores it in Postgres, extracts frames, sends them to a vision LLM for automated damage assessment, and returns results in chat.

This is an MVP. Production will swap Postgres BYTEA for S3, add Redis caching, LangGraph orchestration, and presigned URLs. The chunking logic, ordering, completeness gate, and processing pipeline built here carry forward unchanged.

## Tech stack

- **Python 3.12**
- **Streamlit** — chat UI with video upload
- **PostgreSQL** — metadata + video bytes (BYTEA for MVP; production uses S3)
- **psycopg 3** — async-capable Postgres driver (not psycopg2)
- **ffmpeg** — frame extraction from video (subprocess call, not OpenCV)
- **Anthropic SDK** — Claude Vision for damage assessment

## Architecture (MVP)

```
User (Streamlit chat)
  → Upload video
  → App chunks into 5 MB parts
  → Store chunks in Postgres (video_parts table, BYTEA, with part_number ordering)
  → Completeness gate: all parts confirmed before processing
  → Reassemble video from ordered chunks
  → Extract frames via ffmpeg (write to temp file, extract at 1 fps)
  → Send selected frames to Claude Vision as base64 image content blocks
  → Store assessment in Postgres (damage_assessments table)
  → Return assessment in chat
```

No S3, no Redis, no LangGraph routing, no message queue in this build. Those are deferred to production migration.

## Database schema

### videos
```sql
CREATE TABLE videos (
    video_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id UUID NOT NULL,
    user_id UUID NOT NULL,
    upload_status VARCHAR(20) NOT NULL DEFAULT 'initiated',
    processing_status VARCHAR(20) DEFAULT NULL,
    total_expected_parts INT NOT NULL,
    video_size_bytes BIGINT NOT NULL,
    duration_seconds INT,
    content_type VARCHAR(50) NOT NULL,
    checksum VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    upload_completed_at TIMESTAMPTZ,
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ
);
```

upload_status: initiated → uploading → uploaded → failed
processing_status: NULL → queued → running → succeeded → failed

These are decoupled state machines. Upload status and processing status advance independently.

### video_parts
```sql
CREATE TABLE video_parts (
    part_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(video_id),
    part_number INT NOT NULL,
    data BYTEA NOT NULL,
    part_size_bytes INT NOT NULL,
    checksum VARCHAR(128),
    upload_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    UNIQUE(video_id, part_number)
);
```

Completeness gate: `SELECT COUNT(*) FROM video_parts WHERE video_id = $1 AND upload_status = 'confirmed'` must equal `videos.total_expected_parts` before processing triggers.

Reassembly: `SELECT data FROM video_parts WHERE video_id = $1 ORDER BY part_number` — concatenate the BYTEA results.

### video_frames
```sql
CREATE TABLE video_frames (
    frame_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(video_id),
    frame_index INT NOT NULL,
    timestamp_ms INT NOT NULL,
    data BYTEA NOT NULL,
    width INT,
    height INT,
    mime_type VARCHAR(20) DEFAULT 'image/jpeg',
    selection_status VARCHAR(20) NOT NULL DEFAULT 'extracted',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

selection_status: extracted → filtered_out | selected_for_ai

### damage_assessments
```sql
CREATE TABLE damage_assessments (
    assessment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES videos(video_id),
    claim_id UUID NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(100),
    prompt_version VARCHAR(50),
    assessment_status VARCHAR(20) NOT NULL DEFAULT 'running',
    result_json JSONB,
    summary_text TEXT,
    confidence_score FLOAT,
    input_frame_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
```

## Chunking spec

- Chunk size: 5 MB (5 * 1024 * 1024 bytes)
- Chunks per video: ceil(file_size / chunk_size)
- Last chunk may be smaller than 5 MB
- Each chunk stored as a separate row in video_parts with sequential part_number starting at 1
- Order preserved via part_number column, enforced by UNIQUE(video_id, part_number)
- Reassembly: concatenate BYTEA in part_number order

## Frame extraction spec

- Use ffmpeg via subprocess, not OpenCV
- Write reassembled video to a temp file
- Extract at 1 frame per second: `ffmpeg -i input.mp4 -vf "fps=1" -q:v 2 frame_%04d.jpg`
- Read extracted frame files back as bytes
- Store frame bytes in video_frames table with frame_index and timestamp_ms
- For MVP: send all extracted frames to the vision LLM (no two-pass sampling yet)

## Vision LLM spec

- Use Anthropic SDK with Claude claude-sonnet-4-5-20241022 (vision-capable)
- Frames sent as base64 image content blocks
- System prompt should request structured JSON output: damage_type, severity (1-10), confidence (0-1), description
- Parse response, store in damage_assessments.result_json
- Handle API errors gracefully with user-facing error message in chat

## Coding conventions

- All database operations use parameterized queries (never string interpolation)
- Use `with` context managers for database connections
- Temp files cleaned up in `finally` blocks
- Functions are small and single-purpose
- Error messages shown to user in chat via st.error(), not silent failures
- No hardcoded credentials — use environment variables or st.secrets
- Type hints on all function signatures

## File structure

```
video-claim-mvp/
├── CLAUDE.md
├── .env                    # DB connection string, Anthropic API key
├── requirements.txt
├── app.py                  # Streamlit entry point
├── db/
│   ├── schema.sql          # Table definitions
│   ├── connection.py       # Postgres connection management
│   └── queries.py          # All SQL operations as functions
├── services/
│   ├── chunking.py         # Video chunking + reassembly + completeness gate
│   ├── frames.py           # Frame extraction via ffmpeg
│   └── assessment.py       # Vision LLM integration
└── utils/
    └── validation.py       # File type, size, duration validation
```

## What NOT to do

- Do not store video bytes as base64 text — use BYTEA
- Do not skip the completeness gate — all parts must be confirmed before processing
- Do not send raw video to the LLM — extract frames first
- Do not use psycopg2 — use psycopg (v3)
- Do not hardcode API keys or connection strings
- Do not catch and silently swallow exceptions
- Do not put SQL strings inline in service functions — keep them in db/queries.py
