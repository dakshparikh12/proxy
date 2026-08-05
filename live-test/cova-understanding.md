# Codebase understanding
(Your resident mental model of this codebase — a holistic, qualitative comprehension, like a senior engineer who studied the repo. It is NOT a code index: it does not carry exact line numbers. Use it to understand the system and to know WHICH area to go to; then look up the exact `file:line` LIVE with a targeted search when you need to cite one. The compact navigation map beneath it is the geography — where things live at the area/module level.)

# Cova — Resident Engineer's Understanding

> Written to be the *only* thing a teammate reads before a meeting about this codebase. It is dense and opinionated. Where the code and the docs disagree, **trust the code** — this repo has heavy documentation drift across three product eras, and half the top-level `.md` files describe systems that were deleted or never shipped. Read the "Gotchas" and "Where to go" sections before you grep anything live.

---

## What this is — the product/system

**Cova** is an AI interior-design web app: a user photographs their real room, takes a style quiz, and Cova returns a **photorealistic AI redesign of that exact room** plus a **shoppable shelf** where every detected item links to a real, affiliate-tagged product. Tagline in-repo: "Your room. Reimagined." Domain: **covainterior.com**. Founders: **Daksh Parikh** (backend + AI pipeline) and **Pranav Goel** (strategy + frontend). Supabase project id `xdeacbmkdwqrwxonqaqh`. It is pre-launch, VC-demo-oriented, desktop-first "luxury editorial" dark UI (deep navy + gold + EB Garamond/Playfair serif headings + Inter/DM Sans body).

**The value proposition / differentiators** (as pitched in `COVA_CONTEXT.md`, `PRODUCT_TRUTH.md`): a **Bayesian style "fingerprint"** learned from a swipe/pairwise quiz; **architecture-preserving redesign** (the render keeps your walls/windows/ceiling/kept furniture, unlike competitors that generate a generic room); **keep/remove** with pixel-accurate masks; a **shelf + product-match** commerce layer with affiliate monetization; and a compounding **taste-to-purchase behavioral dataset**. Competitive framing: beats Interior AI (no commerce/personalization), Onton (no fingerprint/keep-remove), Wayfair Muse / IKEA Kreativ (locked to own catalog).

**THE SINGLE MOST IMPORTANT THING TO UNDERSTAND: this repo has lived through THREE product architectures, and fossils of all three are on disk simultaneously.**
1. **Era 1 — 3D Gaussian-splat pipeline (DEAD).** AnySplat/Marble/SparkJS: capture a video → reconstruct a 3D Gaussian splat → redesign the splat → walk the room in 3D with THREE.Sprite hotspots. This is what `CLAUDE.md`, `ARCHITECTURE.md`, `docs/STATUS.md`, and the `pipelineConfig.ts` "modes" describe. **It is retired.** Its code lives in `_archive/modal_pipeline/` and the frozen Supabase Edge Functions; the `marble_*`/splat columns were dropped from the DB. The SparkJS 3D viewer (`app/room/[roomId]/3d`) still exists but is unreachable (renders mock data, zero inbound links).
2. **Era 2 — FLUX Kontext image redesign (LEGACY-but-still-wired).** Single photo → Replicate FLUX Kontext Pro two-pass redesign + Depth Pro + Schnell preview + Clarity upscale, async prediction+poll. This is the `COVA_CONTEXT.md` "9-step" vision and much of `docs/PRODUCT_STATUS.md`. Its routes (`/api/render`, `/api/render/status`, `/api/swap`, `/api/match`, `/api/detect`) still work and are the fallback ("v2") path.
3. **Era 3 — "V9" LoRA render pipeline on Modal (CURRENT / LIVE).** Photo → **perception** (Modal) → **empty-room** (Modal) → **redesign** (Modal, fal.ai flux-general + custom style LoRAs, 3-pass) → **furniture-match** (Serper + Google Lens). Prompts are built by a Claude "Design Director." This is what the `app/design/step-*` flow drives via `/api/pipeline/*`, and what the `cova-plan/` phase system governs. The current build state is tracked in **`cova-plan/PHASE_STATUS.md`** (the freshest doc, 2026-05-03): a ground-up "V1 rebuild" where **P1A (demolition + ops hardening)** and **P1B (auth + legal + account deletion)** are CLOSED, and **P2 (Quiz + Capture wiring) is the next phase, NOT STARTED**. Token billing / Stripe is planned for P5.

So "current maturity": the render brain (perception→empty-room→redesign) is built and benchmarked (Phase 5 beat the "Collov" baseline on 14/15 bench rooms); auth/legal/deletion are production-hardened; but the *end-to-end wiring* of quiz→capture→render→shop through the new pipeline is mid-rebuild, the token economy is a stub, and email sending is flag-gated off.

---

## Architecture — the big picture

Two runtimes, one datastore, many external model providers.

- **`apps/web/`** — the whole product: **Next.js 14 App Router** (React 18, TypeScript strict, Tailwind, Framer Motion, Zustand v5), deployed on **Vercel**. This holds all pages, all API route handlers (the orchestration brain), the AI/prompt libraries, and the Modal service clients. Node/Next `14.2.35`, pnpm workspace, Turborepo.
- **`modal_pipeline/`** — Python GPU/CPU services deployed as **Modal** apps. The three canonical ones are `perception.py` (`cova-perception`, A10G), `empty_room.py` (`cova-empty-room`, A10G), `redesign.py` (`cova-redesign-v3`, CPU-only — all inference is remote fal.ai/Anthropic). Plus `empty_room_gemini.py` (`cova-empty-room-v3`) and `composite_art.py` (`cova-composite-art`). Everything else in that folder is dormant/legacy.
- **Supabase** — the shared substrate: **Postgres** (with pgvector), **Auth** (email/password + Google OAuth), **Storage** (buckets `room-photos`, `renders`, `depth-maps`, `room-videos`, `room-uploads`, landing/quiz asset buckets), and **11 Edge Functions** (all FROZEN legacy from Era 1/2 — the app calls them almost never; `delete-user-cascade` is the live exception).
- **External model/data providers**: **fal.ai** (current primary — SAM3, LaMa, Bria, FLUX Fill/general, IC-Light, nano-banana/Gemini), **Replicate** (legacy FLUX Kontext/Depth/Schnell/Fill, CLIP, GroundingDINO, grounded-SAM — being deprecated), **Anthropic Claude** (Director, chat, vision, refinement classification, quiz tweak, judging), **Google Gemini 2.5 Flash** (room-architecture analysis), **Serper** (Google Shopping) + **SearchApi** (Google Lens) for product matching, **Unsplash** (IP-Adapter reference images), **Cloudflare** (R2 storage + Images + Turnstile captcha), **Upstash Redis** (rate limiting), **Resend** (transactional email), **PostHog** (analytics), **Sentry** (errors).

**Request-shape:** Vercel serverless route handlers own orchestration. Long jobs are handled two ways: (a) Era-2 legacy = submit a **Replicate prediction**, return `prediction_id`, client **polls** `/api/render/status`; (b) Era-3 current = route **awaits the Modal webhook synchronously** (timeouts up to ~7 min via `maxDuration=300` + `AbortSignal`), and the client polls `/api/rooms/status` for the `redesign_status` state field. Vercel route timeout budget is centralized in `lib/config/timeouts.ts` (`VERCEL_ROUTE 290s`, `MODAL_APP 300s`, `REDESIGN_MODAL 420s`, etc.).

**The whiteboard:** Browser (design/step-* pages + Zustand stores) → Next API routes (auth via middleware, cost-logging via `paidCall`) → { Claude Director builds prompt · Modal perception/empty-room/redesign · Serper/Lens match } → Supabase `rooms` row is the durable state machine → client polls back. Style identity is computed client-side + `/api/quiz/fingerprint`, persisted in a cookie-backed Zustand store and mirrored to `users.style_blend`.

---

## End-to-end flows

### Canonical user journey (Era-3, the live `app/design/step-*` chain)
`lib/routes.ts` (the `FLOW`/`getNextRoute` map to `/quiz`,`/viewer`,`/processing`) is **DEAD** — nothing imports it. The real order is hardcoded `router.push("/design/step-N")` across pages, gated by `middleware.ts`:

`/` (landing) → `/auth/signin` → **step-1** (cinematic welcome, no data) → **step-2** (room type + 3-question 2×2 "anchor" quiz that seeds a style prior; `POST /api/quiz/fingerprint` with `anchor_prior`) → **step-3** (full quiz: 3 non-visual questions + up to 24 pairwise comparisons via `POST /api/quiz/next-pair`, then compute; fast-paths to compute if step-2 was confident, `blendRatio ≥ 0.72`) → **step-3b** (confirm/adjust the top-3 style blend as bars; writes `users.style_blend` directly) → **step-4** (the "Style Fingerprint" reveal: name, radar, palette, a live `POST /api/quiz/style-preview` render polled through `/api/render/status?showcase_mode=true`, and a natural-language "Tweak My Style" via `POST /api/quiz/tweak`) → **step-5** (budget tier carousel, maps tier→`{rangeMin,rangeMax}` into `useArea2Store` + `rooms.render_context`) → **step-6** (room photo upload → `POST /api/rooms/capture`; fire-and-forget `POST /api/pipeline/pre-detect`) → **step-7** (cinematic "Analyzing your space" loader; polls `rooms.pre_detection_result` directly) → **step-7p** ("Make it yours" personalization — 6 slot cards all currently disabled/"coming soon"; `POST /api/personalization/save` then `POST /api/pipeline/redesign`) → **step-8a** ("Your room is ready" reveal; polls `rooms.redesign_status`) → **step-8b** (refinement chat: type an instruction or tap an object → `POST /api/render/refinement/detect` then `/apply`) → **step-9** (shoppable "Curated Edit": hero render + hotspots, `POST /api/pipeline/furniture-match`, polls `room_products`) → **/dashboard**.

Durable source of truth from step-6 onward is the Supabase **`rooms`** row, reached via `roomId` carried in `useRoomStore`, a `?roomId=` query param, or the `cova_room_id` cookie. Style identity lives in **`useFingerprintStore`** (cookie-persisted so it survives the OAuth redirect) and is mirrored to `users.style_blend` / `users.style_fingerprint`.

### The redesign pipeline (Era-3, `POST /api/pipeline/redesign`, `maxDuration=300`)
This is **the current redesign orchestrator**. Synchronous, not polling: it awaits the Modal `cova-redesign-v3` webhook and writes `render_url` on return. Requires `COVA_REDESIGN_URL`. Steps: (1) build the prompt via the **Design Director** (`runDesignDirector` = Claude `claude-sonnet-4-6` forced-tool call producing a `DesignBrief`, then Haiku `compilePrompt` → 50–80-word FLUX prompt); fallback `buildAnchorFallbackPrompt`. (2) Gemini `analyzeRoomArchitecture` for preservation; `fetchScoredStyleReferences` (Unsplash, top-3) for IP-Adapter refs. (3) Resolve the **LoRA stack** from `users.style_blend` via `getLoraStackFromBlend`/`getDnaTriggerFromBlend`. (4) EVF-SAM2 (`fal-ai/evf-sam`) for kept-item masks + `assembleDifferentialMap` change-map. (5) An **empty-room validation gate** (Claude Haiku "EMPTY/NOT_EMPTY", fail-open). (6) POST to `${COVA_REDESIGN_URL}/v1/generate_redesign` with `empty_room_url`, `style_blend`, `style_fingerprint`, `personalization`, `budget_max`, `seed`. Modal returns `render_url`; route writes `rooms.render_url` + `redesign_pano_url` + `redesign_status:"complete"`. Persists `design_brief`, `flux_prompt`, `unsplash_references`.

Inside Modal `redesign.py` the render is **3 passes + a Pass 0 planner**: Pass 0 = Claude `claude-sonnet-4-5` vision layout planner (falls back to `ROOM_TYPE_SPECS`); Pass 1 "hero lock" = `fal-ai/flux-general/image-to-image` (strength `0.92`, steps `28`, guidance `3.5`) with the LoRA stack (1 style LoRA + add-details + realism) — ControlNet-depth is *dropped* whenever LoRAs are present (a fal pipeline-load workaround); Pass 2 = per-surface decor (`flux-general`, strength `0.55`); Pass 3 = relight via `fal-ai/iclight-v2` (denoise `0.85`/`0.40`, guidance `5.0`), fails open to Pass 2; then in-process editorial post (warm WB, S-curve, grain, vignette) and a Claude `claude-sonnet-4-5` 10-axis QA scorer producing a composite + 3 suggested refinement chips.

### The empty-room step (two implementations, feature-flagged)
Furniture must be erased before redesign. `POST /api/render/empty-room` is cache-aware and routes by `getRenderPipelineVersion(user.id)` (`lib/features.ts`): non-v3 users get **409 pipeline_mismatch** (handled by the legacy path); v3 users hit Modal `cova-empty-room-v3` (`empty_room_gemini.py`, `fal-ai/nano-banana-pro/edit` = Gemini 3, gated by Claude occlusion/emptiness audits). Cache key = SHA-256 of `imageUrl | canonical(preprocessing) | CACHE_PIPELINE_VERSION("v3.0")` in `empty_room_cache` (30-day TTL); miss charges `BASE_RENDER_TOKEN_COST` (5) tokens. The other, parallel implementation is Modal `cova-empty-room` (`empty_room.py`): SAM3 union of 24 furniture prompts (threshold `0.25`) → depth-edge augmentation → architecture protection (5 arch prompts) → morphological close/dilate → **LaMa-primary** inpainter chain (`lama → bria → flux_fill`), with a coverage router (`>92%` → HTTP 413 `EmptyRoomCoverageTooHighError`; `<1%` → HTTP 422 furniture-not-found) and a patch-SSIM QA gate (≥0.80).

### The legacy render pipeline (Era-2, still wired as "v2")
`POST /api/render` → returns a Replicate `prediction_id`; client polls `GET /api/render/status`. `render/status` is a **state machine**: for the `two_pass` branch it polls pass 1 (FLUX Kontext Pro or Depth Pro), then on success submits pass 2 = `submitArchitectureCorrection` (multi-image Kontext against the original photo + Gemini arch analysis), polls it, runs Clarity upscale (`philz1337x/clarity-upscaler`), writes `render_url`/`redesign_status:"complete"`, inserts a `render_sessions` row, then fires async re-detection (`/api/detect/render`) and furniture matching (`/api/pipeline/furniture-match`, 20 s later). Progressive previews served in order: Schnell → pass 1 → pass 2. Companion legacy routes: `render/from-context`, `render/previews(+status)`, `render/refine`, `render/tweak(+status)`, `swap(+status)` (`black-forest-labs/flux-fill-pro`), `match`, `detect`.

### Detection
Era-2 `POST /api/detect` runs `runDetectionPipeline` = **RAM++ (hardcoded ~75-item vocab, no live endpoint) → GroundingDINO (adaptive box thresholds 0.35→0.25→0.15→0.10) → SAM2 + Depth Pro + Claude Vision room-context** (all steps non-fatal). Writes `keep_mask`, `room_context`. Era-3 detection is Modal `cova-perception` (`/v1/detect_preservation_spec` = SAM3 over 15 keep-class prompts + MediaPipe face + PaddleOCR wall-text; `/v1/encode_sam2_for_cache`; `/v1/depth_anything_v2`) and `/api/pipeline/pre-detect`/`segment-masks` (4-strategy segmentation cascade: layered OneFormer+SAM3 → SAM3-direct → grounded-SAM2 → per-item EVF-SAM2).

### Product matching (commerce)
Era-3 `POST /api/pipeline/furniture-match` (`maxDuration=300`, callable internally with `x-service-key`): atomic idempotency lock on `redesign_status`, Modal Claude-Vision detection of items on the render, per-item `normalizeSearchQuery` → **Serper Shopping** search → `isFurnitureProduct` filter → budget filter (per-item = total/count, 2× ceiling) → top-5 with rank-tier labels, staged-write to `room_products` + `rooms.matched_products`. A matching failure never blocks the render (status resets to `complete`). Era-2 `POST /api/match` is the heavier per-hotspot matcher: Claude `claude-sonnet-4-6` tool_use extracts 3 queries → 5 parallel searches (2× **Google Lens** via SearchApi + 3× Serper) → `rrfMerge` (Reciprocal Rank Fusion k=60 + Levenshtein dedup) → batched **CLIP** embeddings (Replicate `andreasjansson/clip-features`) → `CLIP_THRESHOLD 0.6` → weighted final score (visual+budget+fit+quality + 0.12 both-Lens boost) → top-8, labeled Perfect/Very-Similar/Inspired-By. Satellites: `match/accessories`, `match/coherence` (Haiku coherence check), `match/complete-room`, `match/room-budget`. "Buy" builds an affiliate URL (`lib/affiliates.ts`) and logs an `interactions` row via `/api/interactions`.

### Quiz / fingerprint computation
`POST /api/quiz/fingerprint` — no LLM; a large scoring engine. **V2 path** (6 merged dims): Phase-A grid warm-start + non-visual adjustments + Phase-B pairwise with response-time weighting/momentum/contradiction/randomness penalties → `softmaxStyleMatch`. **V3 fast path** (12 dims, when `anchor_prior` present): `lib/quiz/bayes.ts` diagonal-Gaussian Bayesian posterior (Kalman-gain conjugate updates, `INITIAL_VARIANCE 0.25`, `OBSERVATION_VARIANCE 0.15`) over the 10 archetypes in `lib/quiz/style-archetypes.ts`. Saves `style_fingerprints` and mirrors `users.style_fingerprint`/`style_blend`. `next-pair` selects the max-variance dimension (`TOTAL_PAIRS 24`, `MAX_PER_DIM 4`) from `quiz_comparison_pairs`; `next-card`/`mood-board`/`mood-strip` serve `quiz_anchor_images`.

### Auth flow (Era-3, P1B, production-hardened)
`middleware.ts` refreshes the Supabase session on every request and gates `PROTECTED_PAGE_ROUTES` (`/design/step-2..9`, `/dashboard`) → redirect to `/auth/signin?next=…`; protected `/api/*` → 401 JSON; authed user on `/` → `/dashboard`. Signup: per-IP Upstash rate-limit (3/h) → Cloudflare **Turnstile** verify → zxcvbn password strength (`PASSWORD_SCORE_MIN 2`) → `supabase.auth.signUp` → Resend verification email (or admin auto-verify when `EMAIL_SENDING_ENABLED!=="true"`). Signin: Upstash rate-limit + **silent lockout** (5 fails/15 min, always returns generic "invalid credentials"). Google OAuth exchanged at `app/auth/callback/route.ts`. Account deletion (`/api/auth/delete-account`) invokes the `delete-user-cascade` Edge Function and writes an `account_deletions` audit row; GDPR export (`/api/users/export`) builds a JSZip and emails a 7-day signed URL. **No Apple sign-in** (deferred).

### Design-chat (Area-2 conversational context builder)
`POST /api/design-chat` (`maxDuration=90`, Claude `claude-sonnet-4-6`, non-streaming) powers Screen D's 6 tabs (products/tech/art/sensory/identity/budget/all). Per-tab system prompts (`buildSystemPrompt` injects RoomContext/constraints/kept items), base64 image attachments with SHA-256 dedup, product-URL scraping, sentinel tags `[TAB_COMPLETE]`/`[GENERATE_ART:…]` parsed by `extractStructuredData` (a second Sonnet call → strict JSON per tab schema), fire-and-forget art generation. The **product chatbot** is separate: `POST /api/chat` streams `claude-sonnet-4-20250514` as `text/plain`, embeds `<products>` JSON, consumed by `components/chat/ChatPanel.tsx`.

---

## Data model — the real schema

Reconstructed from `supabase/migrations/` (93 files; the generated `lib/types/supabase.ts` is **STALE** — it predates the P1A `20260502203*` demolition batch). Postgres + pgvector, RLS on everywhere.

**Core pipeline tables**
- **`rooms`** — the central entity and pipeline state machine. One row per uploaded room. Key columns: `user_id`(FK), `room_type` (CHECK: living_room/bedroom/kitchen/bathroom/office/dining), `budget`. **`redesign_status`** TEXT default `'pending'` (no DB CHECK; app-enforced) is the main state machine: documented flow `pending → uploading_photos → generating_mini_world → mini_world_ready → awaiting_furniture_selection → kontext_processing → kontext_complete → depth_processing → video_processing → video_ready → matching_products → plus_processing → complete` (note: the mini_world/video states are Era-1 fossils; the live path uses pending→kontext_processing→matching_products→complete). Also `status` (free text), `detection_status` (CHECK pending/running/complete/failed). Artifact URLs: `original_photo_url`, `empty_room_url`, `render_url`, `latest_render_url`, `preview_url`, `redesign_pano_url`, `inpainting_mask_url`, `parallax_video_url`, `perspective_render_url`, `change_map_url`, `canny_map_url`. Director outputs: `design_brief`(JSONB), `flux_prompt`, `aesthetic_score`, `unsplash_references`. Style/context: **`style_blend`(JSONB, NOT NULL)**, `final_style_vector`, `fingerprint_id`(FK), `personalization`, `design_chat_history`, `render_context`(JSONB — the big Area-2 blob), `room_context`, `room_constraints`, `keep_mask`, `pre_detection_result`, `render_detection_result`, `room_analysis`, `matched_products`, `pipeline_cost_log`(JSONB — holds pass ids/prompts). **Dropped by `20260502203100`**: all `marble_*`/`splat_*`/`plus_*`/`flythrough_*`/`depth_map_*` columns (Era-1 3D retired).
- **`renders`** — individual generated images + quality scores. `status` CHECK (pending/processing/complete/completed/failed/quality_rejected), `stage`, `quality_tier` (draft/hd), `style_applied`, `style_blend_ratio`, and a whole quality-metric column family (`brisque_score`, `clip_style_score`, `composite_score`, `hps_score`, `laion_score`, `q_align_score`), generation params (`seed`, `guidance_scale`, `denoising_strength`, `loras_used`), provider ids (`replicate_prediction_id`, `modal_job_id`), and `tokens_used`/`tokens_charged`.
- **`render_sessions`** — iteration history per room (`iteration`, `prompt`, `render_url`, `style_strength`). This is what the render routes insert on completion; gated via parent-room ownership in RLS.
- **`empty_room_cache`** — dedup: `input_hash`(unique), `empty_room_url`, `residual_items`, `pipeline_path`, `quality_warning`, `expires_at`. RPC `prune_expired_empty_room_cache()`.
- **`sam_embeddings`** — cached SAM2 image embeddings; `embedding` with a `octet_length <= 2097152` (2 MB) CHECK, `expires_at`, per-user.

**Products & matching**
- **`products_v1`** — the LIVE catalog (all FKs point here; the migrations only ever `CREATE TABLE products`, the rename happened directly in the DB). `retailer`, `category`, `price`, `price_tier` (budget/mid/premium/luxury), `primary_style`, `style_tags`(text[]), `room_types`(text[]), dimensions, `affiliate_url`, `in_stock`. **Two `vector(768)` embedding columns**: `clip_embedding` and `clip_fusion_embedding` (dim history churned 768→1024→768). ~15,900 rows historically.
- **`products`** (legacy) — older experimental multi-model table (`dinov3_embedding`, `siglip2_embedding` as JSONB). Not authoritative.
- **`room_products`** — matched furniture per room: `product_id`(FK products_v1, nullable), `rank`, `furniture_label`, `match_score`, hotspot geometry (`hotspot_x/y`, `azimuth_degrees`, `elevation_degrees`), and live-match **`serp_*`** columns (`serp_title/image_url/price/product_url/retailer`).
- **`saved_products`** — wishlist (`product_url`, `product_data`).

**Style / quiz**
- **`style_fingerprints`** — quiz output: `vector_data`, `covariance`, `dimensions`, `confidence`, `style_name`, `fingerprint_version`, `quiz_responses`.
- **`users`** — `id`(=auth.users.id), `email`, `auth_provider`, `account_type` (consumer/realtor), `onboarding_completed`, `stripe_customer_id`. Tokens: **`token_balance` INT default 5, `tokens_enabled`**. Style: **`style_blend`(JSONB)**, `style_blend_v3`, `style_fingerprint`, `budget_dna`.
- **`style_vectors`** — reference anchor vectors. **`style_loras`** — trained fal.ai LoRA registry (`trigger_word`, `full_trigger`, `lora_url`, `recommended_scale`, `quality_score`, `validation_urls`).
- **`swipe_history`** — the LIVE quiz interaction log (`card_style`, `direction`, `dwell_time_ms`, `style_vector_before/after`). **`quiz_anchor_images`** (style_index 1–10, `style_vector`, `image_url`, `round_images`) and **`quiz_comparison_pairs`** (dimension pairs with `image_a/b_url`, `_vector`, `_stop`) — the LIVE image-library quiz tables.
- **`redesign_feedback`** — post-render signal (`feedback_score` 1–5, `hotspots_clicked`, `time_spent_in_viewer_ms`).

**Chat / infra**
- **`chat_messages`** — per-room chat (`role` user/assistant, `content`, `image_url`, `tap_x/y`, `tool_calls`, `tool_results`).
- **`render_cost_log`** — **recreated** by `20260502203300` (types file shows the OLD shape). Current: `user_id`, `render_id`, **`category` TEXT NOT NULL** (validated app-side by the TS union in `lib/cost/categories.ts` — no DB enum), `stage`, `cost_usd numeric(8,4)`, `model`, `duration_ms`, `failure_category`. Service-role write, self-read.
- **`feature_flags`** — `key`(unique), `enabled`, `percent_rollout`(0–100), `environment` (development/preview/production/all).
- **`account_deletions`** — GDPR/CCPA audit (`hashed_user_id`, `deletion_reason`, `audit_retention_days` default 30).
- **`app_config`** (key/JSONB), **`rate_limits`** (sliding window, RPC `check_rate_limit`).

**Tables DROPPED by later migrations** (present in the stale types file — do NOT rely on them): `swipe_cards`, `style_references` (dropped `20260408100000`); and by `20260502203200`: `cart_items`, `click_events`, `furniture_placements`, `jobs`, `votes`, `listings`, `product_impressions`, `product_interactions`, `style_purchase_signals`, `rate_limit_events`, `render_comparison_pairs`, `chat_sessions`, `token_transactions`, `render_retries`, `render_rate_limits`, `anchor_renders`, `user_style_profiles`. This killed the realtor subsystem (`listings`/`votes`), the affiliate click/cart tracking, the old `chat_sessions`, and the token ledger `token_transactions`. `quiz_cards` **never existed**.

**pgvector state:** `products_v1` has the two `vector(768)` columns but **no live ANN index** — `20260502203000` dropped the HNSW indexes (vector ANN search was superseded by SERP live-matching). RPCs `search_products_by_embedding`/`search_products_by_fusion_embedding` still exist but now run unindexed.

**RLS:** consolidated (`20260502203500`) into single `FOR ALL` self-owner policies (`auth.uid()=user_id`) on `users/rooms/renders/chat_messages/style_fingerprints/interactions/swipe_history/redesign_feedback/saved_products/sam_embeddings`; `render_sessions` via parent-room EXISTS; reference and audit tables are service-role-write.

**Edge Functions** (all FROZEN legacy, `supabase/functions/`): `aggregate-style-profiles`, `chat-room`, `clip-embed`, `delete-account`, `delete-user-cascade`, `detect-furniture`, `extract-colors`, `generate-depth-map`, `get-render-status`, `match-products`, `render-room`. Only permitted change ever was the `chat-room` model name.

---

## Subsystems

**Design Director (`lib/ai/design-director.ts`)** — the render brain, "Call 1" of a two-call pipeline. Claude `claude-sonnet-4-6`, `max_tokens 4096`, forced `tool_choice` on `emit_design_brief` (guaranteed structured output). Produces a **`DesignBrief`**: `room_type`, `architecture_preserved`, `anchor` (hero piece matching room type), `palette` (60-30-10), `three_materials`, `lighting_concept`, `depth_zones`, `object_hierarchy` (hero + supporting + accessories), `room_scale` (small<12m² max 4 pieces / medium max 7 / large max 10), `hard_constraints`, `smart_suggestions`, and **`style_weights`** carrying `primary_lora`/`primary_scale` (back-compat) plus `loras: Record<cvTrigger, number>` (the weighted blend summing to 1.0). LoRA-blend precedence: `RenderContext.style_blend` → Sonnet-echoed `loras` → fallback `{primaryTrigger:1.0}`. `runDesignDirector` chains it into `compilePrompt`. Layer-4 forbidden-name defense = a Zod `.refine` on free-text fields.

**Prompt compiler (`lib/ai/prompt-compiler.ts`)** — "Call 2". Claude `claude-haiku-4-5-20251001`, temp 0.3, targets **50–80 words** in a 7-token order (trigger → room+structure → 3 materials → object hierarchy → lighting → camera → mood), validates word count + LoRA-trigger presence + hero item, retries once, runs `sanitizeForbiddenNames` (Layer 3). `ANTI_CLUTTER_RULES` per style.

**LoRA system** — three cooperating files. `lib/styles/lora-registry.ts` is the **auto-generated registry of the 10 trained archetypes** (`LORA_REGISTRY`, each `{fal.media url, trigger, default_scale}`): `COVASCANDINAV→cvscn(0.88)`, `COVAJAPANDI→cvwmn(0.88)`, `COVABIOPHILIC→cvbio(0.92)`, `COVAMINIMAL→cvmin(0.95)`, `COVAMIDCENTURY→cvmcm(0.85)`, `COVACOASTAL→cvcst(0.85)`, `COVABOHEMIAN→cvboh(0.78)`, `COVAINDUSTRIAL→cvind(0.80)`, `COVAFARMHOUSE→cvfrm(0.85)`, `COVAARTDECO→cvdco(0.82)`; scale formula `default_scale × blend_weight × 0.85`, top-3. `lib/ai/lora-blending.ts` enforces the **max-3 / drop-below-0.10 / normalize-to-1.0** contract. `lib/ai/lora-registry.ts` (distinct) is the DB-backed resolver against `style_loras`, with `FALLBACK_LORA_STACK` (detail-enhancer 0.55 + realism 0.25), `MAX_COMBINED_LORA_SCALE 1.2`, and `BACKUP_URLS`. The Modal `redesign.py` mirrors `LORA_REGISTRY` and caps effective primary LoRA scale at **0.65** so prompt personalization isn't overridden.

**Style archetypes & fingerprint (`lib/quiz/style-archetypes.ts`, `lib/ai/style-match.ts`, `types/fingerprint.ts`)** — the fingerprint has THREE historical versions coexisting in one interface. V1 = 12 dims `[-1,1]`; V2 = 6 merged dims `[0,1]` (colorTemperature→warmth, etc., slots 7–12 = 0.5 unused); **V3 = 12 dims `[0,1]`** with `DIMENSION_ORDER` = warmth, complexity, formality, natureMateriality, colorSaturation, textureRichness, lightingDrama, pattern, lightPreference, lineQuality, symmetryPreference, plantPresence. `fingerprintToStyleBlend` = softmax (temperature 0.35) over cosine similarity to the 10 archetype vectors, filtered >0.10, renormalized → `style_blend`. `softmaxStyleMatch` (T=4.5) yields primary/secondary/blendRatio.

**RenderContext (`lib/ai/renderContext/`, `lib/types/area2.ts`)** — the "Area 2" master brief assembled from Screens A–D: `RoomContext` (shape/ceiling/light/materials/camera), `RoomConstraints`, `KeepMask` (DetectedObject[] with keep/immovable), `BudgetProfile`, `RequestedProduct[]`, `ArtBrief`, `TechSpec`, `SensoryProfile`, `IdentityBrief`, `tabStates`, `fingerprint`. `assembler.ts` writes BOTH `style_blend` (snake, for Director/DB) and `styleBlend` (camel, for store) from a v3 fingerprint; `persist.ts` writes it to `rooms.render_context` + flattened `personalization` + `design_step:4`.

**Detection (`lib/ai/detection/`)** — `index.ts` orchestrates RAM++→GroundingDINO→SAM2 with parallel Depth Pro + Claude room-context. `grounding.ts` (adaptive box thresholds), `sam.ts` (grounded_sam), `depth.ts` (Depth Pro model with an Apache-2.0 Depth-Anything-V2 fallback + a hand-rolled PNG decoder, `DECODE_STEP 4` = 16× downsample), `ram.ts` (hardcoded ~75-item vocab, documented as a stand-in for real RAM++), `roomContext.ts`/`objectMaterial.ts` (Claude vision). Confidence calibration is dynamic (`showUser = max(0.25, top×0.70)`). Note: top-level `lib/ai/grounding.ts` and `lib/ai/depth.ts` are OLDER duplicates — the `detection/` folder is current.

**FLUX submitters (`lib/ai/flux.ts`)** — 1700-line module of prompt builders (`buildFluxPrompt`, `buildFluxPromptFromContext`, `buildAnchorFallbackPrompt`, `getMaterialPalette`) + Replicate/fal submitters. Model constants: `KONTEXT_MODEL` (flux-kontext-pro), `SCHNELL_MODEL`, `MULTI_KONTEXT_MODEL`, `DEPTH_MODEL` (flux-depth-pro), `UPSCALER_MODEL` (clarity-upscaler). Has its OWN forbidden-name sanitizers too. `NEUTRAL_MIDPOINT 0.5`, `NEUTRAL_BAND 0.1`, `MAX_WORDS 350`.

**Modal services (`modal_pipeline/`)** — `perception.py` (cova-perception, A10G): SAM3 15 keep-class prompts + SAM2 encoder + MediaPipe face + PaddleOCR wall-text + depth passthrough. `empty_room.py` (cova-empty-room, A10G): SAM3-24-prompt furniture erase, LaMa-primary inpainter, coverage router, SSIM QA. `redesign.py` (**cova-redesign-v3**, CPU): 3-pass flux-general + LoRA + IC-Light relight + Claude QA. `empty_room_gemini.py` (cova-empty-room-v3): nano-banana/Gemini-3 empty-room. `composite_art.py` (cova-composite-art): SAM3 wall detect + homography + Poisson blend + IC-Light harmonize. TS clients: `perception-client.ts`, `empty-room-client.ts` (with `EmptyRoomCoverageTooHighError`), `redesign-client.ts` (with `RedesignBadRequestError`/`RedesignUpstreamError`). Everything else in the folder (`embed_*`, `scrape_catalog`, `insert_products`, `mask_build`, `postprocess`, `scoring`, `spatial_templates`, `empty_room_v3.py`) is dormant/legacy.

**Auth (`middleware.ts`, `lib/supabase/*`, `lib/auth/*`, `app/api/auth/*`)** — see the auth flow above. Three Supabase client factories, MUST use them (never import `@supabase/supabase-js` directly): `createBrowserClient` (client components), `createServerSupabaseClient` (routes/RSC, anon+RLS), `createAdminSupabaseClient` (service-role, RLS bypass). `lib/supabase/storage.ts` owns `STORAGE_BUCKETS` and path building.

**Cost tracking (`lib/cost/*`, `lib/paid/call.ts`)** — every paid provider call SHOULD go through `paidCall({provider, url, init, costEstimate})`, which always `logCost` in a `finally` (duration + failure category) into `render_cost_log`. `CostCategory` is a TS union (the DB column is plain text). ESLint rule `no-direct-paid-fetch` enforces this. Admin dashboard `/api/admin/render-costs` aggregates 24h/7d/30d; `/api/cron/cost-monitor` (Bearer `CRON_SECRET`) is a spend ceiling-breaker. Only real rate constant on disk: `RESEND_BLENDED_RATE_USD 0.0008`.

**Feature flags & pipeline selection (`lib/feature-flags/index.ts`, `lib/features.ts`)** — `feature_flags` DB table with 30s cache + `percent_rollout` hash bucketing. `getRenderPipelineVersion(userId)` picks v2/v3 by precedence: `COVA_RENDER_PIPELINE` hard-pin → `ADMIN_TEST_USER_IDS` (always v3) → `COVA_V3_ROLLOUT_PERCENTAGE` (FNV-1a bucket) → **default v2**. `lib/pipelineConfig.ts` (`PIPELINE_MODE`) and `lib/render-config.ts` (`[REDACTED]` token/cache constants) also live here; the former is Era-1 legacy.

**Rate limiting** — TWO implementations. Current: `lib/rate-limit/upstash.ts` (Upstash Redis, sliding window, key prefix `cova:p01b`, per-route limits e.g. signup 3/h, signin 20/h, users-export 1/24h, silent-signin-lockout 5/15min, per-email cooldowns), enforced in `middleware.ts` before session refresh. Legacy: `lib/rateLimit.ts` (in-memory Map, single-instance, used by render/match/detect routes: render 5/h, match 30/h, detect 10/h). Plus a DB `check_rate_limit` RPC. Three overlapping systems.

**Logging & errors (`lib/logger.ts`, `lib/errors.ts`, `lib/log/redact.ts`, `lib/logging/redactor.ts`)** — `CovaLogger` is dev-only NDJSON, **full no-op in production**; browser relays to `/api/dev-log`. `CovaError` subclasses + `captureError()` which **redacts before Sentry**. `redactLogPayload` strips PII (email/phone/address regex), `FORBIDDEN_KEYS` (password/tokens/ssn/…) → `[redacted]`, hashes `userId`, and finally scrubs forbidden style names. ESLint `no-unsafe-console` forbids logging non-literal args unless wrapped in `redactLogPayload`.

**Forbidden-name enforcement (`lib/ai/forbidden-names.ts`, `canonical-archetypes.ts`, `anchor-display.ts`)** — CLAUDE.md Rule 8: the bare style tokens **Japandi, Bohemian, Coastal, Mid-Century Modern, Minimalist, Scandinavian** must never reach the user; qualified forms ("Warm Japandi", "Coastal Bright", "Retro Mid-Century") are allowed. Five defense layers: ESLint rule (author), Husky pre-commit (commit), runtime sanitizer (Layer 3), Zod refine on Sonnet output (Layer 4), log redactor (Layer 5). UI labels route through `anchor-display.ts` display-name maps and `canonical-archetypes.ts` (9 safe names like "Warm & Grounded", "Pure Minimal White").

**Analytics (`lib/analytics*`, PostHog)** — TWO init paths that disagree on host (`app.posthog.com` vs `eu.posthog.com`); both set `capture_pageview:false`, `autocapture:false`, no-op without a key. Product events: `quiz_started/completed`, `photo_uploaded`, `render_started/completed`, `hotspot_tapped`, `affiliate_clicked`, `match_viewed`, `swap_initiated`. Separate internal ops taxonomy in `lib/analytics/events.ts` (`subagent.run.*`, `cost_alert`, `phase.deploy`).

**Email (`lib/email/resend.ts`)** — `sendAuthEmail` gated by `EMAIL_SENDING_ENABLED==="true"` (returns synthetic success when off — the current default), Resend via `paidCall`, `RESEND_FROM = "Cova <auth@send.covainterior.com>"`, react-email templates.

**Media (`lib/media/cloudflare.ts`, `lib/unsplash.ts`)** — Cloudflare R2 (S3-compatible, default bucket `cova-renders`) + Cloudflare Images; `r2SignedGetUrl` default 1h. Unsplash provides **reference images for IP-Adapter mood/color/material (not layout)** — `fetchScoredStyleReferences` scores on orientation/resolution/likes/keyword, returns `[]` if no key (never blocks a render).

**Commerce / affiliates (`lib/affiliates.ts`)** — `buildAffiliateUrl(url, retailer, asin?)` per network: Amazon (`?tag=cova03-20`, ASIN-extracting — the only ACTIVE program), IKEA (no program, passthrough), Article (AWC), Crate&Barrel/CB2 (Sovrn/viglink), West Elm/Pottery Barn (ShareASale), Ruggable/One Kings Lane/Overstock (CJ/anrdoezrs), Etsy (Awin). All non-Amazon IDs are env vars, mostly unset (pending program approval), degrading to the raw URL.

**State stores** — current set is `lib/store/`: `useRoomStore` (in-memory, NOT sessionStorage per Hard Rule 1), `useFingerprintStore` (custom **cookie** adapter `cova-fingerprint-v1` so it survives OAuth redirects; covariance stripped to stay under cookie size), `useSessionStore` (sessionStorage, barely used), `useRefineStore` (dead). Plus `lib/stores/area2Store.ts` `useArea2Store` (LIVE Screen-D store, 9 importers) — confusingly it sits in the "legacy" folder. Dead in `lib/stores/`: `userStore.ts`, `roomStore.ts` (name-collides with the live `store/room` one), `cameraStore.ts` (only the dead 3D viewer uses it).

---

## Key concepts & domain models

- **StyleFingerprint / style_blend** — the fingerprint is a per-dimension vector; `style_blend` is its softmax projection onto the 10 named archetypes (`{COVAJAPANDI:0.7, COVASCANDINAV:0.3}`), which drives the LoRA stack. This is the through-line from quiz → render. Invariant: max 3 LoRAs, weights sum to 1.0, drop <0.10.
- **cv-triggers** — 3–5-char LoRA trigger words (`cvwmn`,`cvscn`,`cvbio`,`cvmcm`,`cvcst`,`cvboh`,`cvind`,`cvfrm`,`cvdco`,`cvmin`) that map to the COVA* archetype keys. NAMING IS INCONSISTENT across files (research docs use `cvjpd/cvlux/cvmax`; `canonical-archetypes.ts` folds COVAMINIMAL+COVASCANDINAV together; `COVAJAPANDI→cvwmn` not `cvjpd`). Reconciliation was deferred (see CLAUDE.md Phase-3 handoff).
- **DesignBrief** — the Director's structured decision (anchor/palette/materials/object hierarchy/lighting/style_weights). It is Call 1; the FLUX prompt is Call 2.
- **RenderContext** — the assembled Area-2 brief (RoomContext/Constraints/KeepMask/Budget/Art/Tech/Sensory/Identity + fingerprint), stored in `rooms.render_context`; the render prompt is rebuilt fresh from it each call.
- **KeepMask / DetectedObject** — detected furniture with `keep`/`immovable` flags and segmentation masks; drives whether a redesign preserves or replaces each item.
- **redesign_status** — the room's pipeline state field, the client's polling target (via `/api/rooms/status`).
- **v2 vs v3 render pipeline** — a per-user feature-flag fork between the legacy Replicate path and the current Modal path.
- **Tokens** — a planned credit economy (`BASE_RENDER_TOKEN_COST 5`, free/paid retries) — currently a STUB (see gotchas).

---

## Integrations & external services

- **fal.ai** (`FAL_KEY`) — current primary inference: SAM3 (`fal-ai/sam-3/image`), LaMa (`fal-ai/lama`), Bria (`fal-ai/bria/eraser`), FLUX Fill (`fal-ai/flux-pro/v1/fill`), FLUX general (`fal-ai/flux-general/image-to-image`), Kontext (`fal-ai/flux-kontext-pro`), IC-Light (`fal-ai/iclight-v2`), EVF-SAM/SAM2 (`fal-ai/evf-sam`, `fal-ai/sam2/image`), Schnell (`fal-ai/flux/schnell`), nano-banana/Gemini-3 (`fal-ai/nano-banana-pro/edit`). Browser→fal proxy at `/api/fal/proxy`.
- **Replicate** (`REPLICATE_API_TOKEN`, marked "legacy, deprecating") — FLUX Kontext/Depth/Schnell/Fill Pro, `flux-1.1-pro` (quiz validation), GroundingDINO (`adirik/grounding-dino`), grounded_sam (`schananas/grounded_sam`), Depth Pro / Depth-Anything-V2, CLIP (`andreasjansson/clip-features`), Clarity upscaler.
- **Anthropic Claude** (`[REDACTED]`) — Director (`claude-sonnet-4-6`), compiler/refine-classify/coherence/tweak/explain/empty-room-gate (`claude-haiku-4-5-20251001`), product chat stream (`claude-sonnet-4-20250514`), complete-room (`claude-sonnet-4-5`), Modal layout planner + QA judge (`claude-sonnet-4-5`).
- **Google Gemini 2.5 Flash** (`GEMINI_API_KEY`) — `analyzeRoomArchitecture` (walls/floor/ceiling/camera/fixed elements) feeding preservation clauses.
- **Serper** (`SERPER_API_KEY`, `google.serper.dev/shopping`) + **SearchApi** (`SEARCHAPI_KEY`, Google Lens `engine=google_lens`) — product matching.
- **Supabase** — Postgres/Auth/Storage/Edge Functions.
- **Cloudflare** — R2 + Images (`CLOUDFLARE_ACCOUNT_ID`, `R2_*`, `CLOUDFLARE_IMAGES_API_TOKEN`) + **Turnstile** captcha (`TURNSTILE_SECRET_KEY`, `NEXT_PUBLIC_TURNSTILE_SITE_KEY`).
- **Upstash Redis** (`UPSTASH_REDIS_REST_URL/_TOKEN`) — rate limiting.
- **Resend** (`RESEND_API_KEY`) — transactional email.
- **Unsplash** (`UNSPLASH_ACCESS_KEY`) — reference images.
- **PostHog** (`NEXT_PUBLIC_[REDACTED]/_HOST`) + **Sentry** (`SENTRY_DSN`).
- **Affiliate networks** — Amazon Associates (active), Impact/Sovrn/ShareASale/CJ/Awin (pending).
- **Modal** (`MODAL_TOKEN_ID/_SECRET` for deploy) — the perception/empty-room/redesign apps, reached from the app via `COVA_PERCEPTION_URL`, `COVA_EMPTY_ROOM_URL`, `COVA_EMPTY_ROOM_V3_URL`, `COVA_REDESIGN_URL`, and webhook vars `PRE_DETECTION_WEBHOOK_URL`, `FURNITURE_MATCHING_WEBHOOK_URL`, `LAYERED_SEGMENTATION_URL`, `GROUNDED_SAM2_URL`, `DEPTHFLOW_WEBHOOK_URL`.

---

## Config & environments

`docs/ENVIRONMENT_VARIABLES.md` is stale (lists unused `MODAL_RENDER/STATUS_ENDPOINT`, omits ~40 real vars). Authoritative surface is `lib/env.ts` `validateEnvironment()` + code grep.

- **Required (throws in prod):** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `[REDACTED]`, `FAL_KEY`.
- **Optional / feature-gating:** `REPLICATE_API_TOKEN` (legacy), `GEMINI_API_KEY`, `SERPER_API_KEY`, `SEARCHAPI_KEY`, `UNSPLASH_ACCESS_KEY`.
- **Render pipeline / flags:** `COVA_REDESIGN_URL`, `COVA_EMPTY_ROOM_URL`, `COVA_EMPTY_ROOM_V3_URL`, `COVA_PERCEPTION_URL`, plus the webhook URLs above; `COVA_RENDER_PIPELINE` (hard-pin v2/v3), `COVA_V3_ROLLOUT_PERCENTAGE`, `ADMIN_TEST_USER_IDS`, `PIPELINE_MODE`.
- **Infra:** `UPSTASH_REDIS_REST_URL/_TOKEN`, `TURNSTILE_SECRET_KEY`+`NEXT_PUBLIC_TURNSTILE_SITE_KEY`, `EMAIL_SENDING_ENABLED` (default off), `RESEND_API_KEY`, `CRON_SECRET`, `NEXT_PUBLIC_APP_URL`, `CLOUDFLARE_ACCOUNT_ID`, `R2_*`, `CLOUDFLARE_IMAGES_API_TOKEN`.
- **Observability:** `NEXT_PUBLIC_[REDACTED]/_HOST`, `SENTRY_DSN` (+`NEXT_PUBLIC_SENTRY_DSN`).
- **Affiliates:** `AMAZON_AFFILIATE_TAG` (=cova03-20) + per-retailer `*_AFFILIATE_ID`.
- **Timeouts** centralized in `lib/config/timeouts.ts` (ESLint-enforced). **Token/cache/quality knobs** in `lib/render-config.ts` (`BASE_RENDER_TOKEN_COST 5`, `CACHE_TTL_DAYS 30`, `CACHE_PIPELINE_VERSION "v3.0"`, `QUALITY_WARNING_SCORE_THRESHOLD 7`).
- **Build/test:** pnpm workspace + Turborepo; web app scripts `next dev/build/start`, `vitest` unit, `playwright` e2e/smoke (runs against `next start` production build, NEVER `next dev`). Husky pre-commit runs the forbidden-name hook. Custom ESLint plugin `eslint-plugin-cova-rules` (`no-unsafe-console`, `no-direct-paid-fetch`, `no-restricted-syntax` for forbidden names).

---

## Conventions & patterns

- **Supabase client discipline** — always the three `lib/supabase/*` wrappers, never `@supabase/supabase-js` directly; anon client respects RLS, admin client bypasses it (use only server-side for cross-user/storage ops).
- **API route pattern** — `getUser()` first → 401 if none → verify ownership (`.eq("user_id", user.id)`) → rate-limit → do work → typed `NextResponse.json`. Internal service-to-service calls authenticate with `x-service-key`/`x-dev-bypass` == service-role key.
- **Paid calls** go through `paidCall` (cost-logged); enforced by ESLint. Long jobs use `maxDuration` + `AbortSignal.timeout(TIMEOUTS.*)`.
- **Never show style names** (Hard Rule 8) — 5-layer defense; all UI labels via `anchor-display`.
- **No localStorage/sessionStorage for auth/fingerprint** (Hard Rule 1) — auth is HTTP-only cookies via `@supabase/ssr`; fingerprint store uses a cookie adapter; the sessionStorage fallback was removed.
- **SparkJS/Three** must be dynamically imported `ssr:false` and never imported at page module level (Hard Rules 2,3) — a build-breaker (though the 3D viewer is now dead).
- **Design tokens** — colors/fonts via CSS variables in `styles/tokens.css` (`--cova-*`); dark theme only; shadcn overrides live in `components/cova/`.
- **The phase workflow** — the current rebuild is governed by `cova-plan/`: Planner → Executor → two independent Verifier sessions → manual gate → CLOSED, with drift docs, back-bugs, escalations, and per-phase feature-flag plans. Treat `cova-plan/PHASE_STATUS.md` as the freshest truth and "code over docs" as the standing rule.
- **Typed cost categories / failure categories** are TS unions extended per-phase without DB migrations.

---

## Gotchas & footguns

- **Three product eras on disk at once.** Do not model the system from `CLAUDE.md`/`ARCHITECTURE.md`/`docs/STATUS.md` — they describe the dead AnySplat/Marble 3D pipeline and the "9-step FLUX.2 [pro]" vision (there is no FLUX.2-pro in code; it's fal flux-general + LoRAs). The `PIPELINE_MODE`/`pipelineConfig.ts` "marble/pansplat" modes are Era-1 fossils.
- **`_archive/` is dead code.** `_archive/modal_pipeline/` (AnySplat, marble_*, redesign_pipeline, furniture_matching, depth_video, clip_text_embed, generation.py) and `_archive/web/` — never read them to understand the live system.
- **`apps/mobile/` is FROZEN** — never touch (Hard Rule 11). The root `package.json` is actually the Expo/React-Native mobile manifest; the web app is `apps/web/package.json`.
- **The 11 Supabase Edge Functions are FROZEN legacy** and mostly unused by the live app.
- **Two render pipelines coexist, forked per-user by a flag.** `/api/render/empty-room` literally 409s "v2" users back to the legacy path. Default is **v2**. Know which one a given user is on before debugging a render.
- **Two empty-room *and* two redesign implementations.** Modal `cova-empty-room` (LaMa, `empty_room.py`) vs `cova-empty-room-v3` (Gemini, `empty_room_gemini.py`); the redesign app is named **`cova-redesign-v3`** in code (not `cova-redesign` as docs say).
- **Modal docstring drift:** `empty_room.py` advertises flux_fill as primary inpainter but the code makes **LaMa** primary; `redesign.py` top docstring quotes stale Pass-3 denoise `0.88/0.45` (real constants `0.85/0.40`); its "FLUX Fill Pro" passes actually run on `flux-general` and drop ControlNet whenever LoRAs are present.
- **The token economy is a STUB.** `lib/tokens.ts` is **in-memory, non-persisted** (admins 999,999; everyone else 100 per process, resets each serverless cold start). `users.token_balance` exists but the RPC `deduct_tokens_atomic` writes to `token_transactions` which was **dropped** — so the DB token path is half-broken. Billing/Stripe is planned P5, not built.
- **step-6's inline QR-to-phone path is dead/broken** — it calls `/api/session` and `/api/poll/{sessionId}` which **do not exist** on disk. The working QR handoff is `capture/generate-token` (JWT via `jose`, 1h, `SUPABASE_JWT_SECRET`) → `/capture/mobile/[token]` → `capture/validate-token` → `/api/rooms/capture`. Photo upload is the reliable path.
- **`lib/routes.ts` is dead** (nothing imports `FLOW`/`getNextRoute`); real navigation is hardcoded `step-*` pushes.
- **`app/design/_future/step-7b..7h` are retired** and moved out of the router so Next won't compile them — but **stale comments** across `pipeline/redesign`, `pipeline/segment-masks`, `design-director.ts`, `ram.ts`, and `step-7` still reference "step-7c/7d/…" as if live. Ignore them; `step-7p` replaced all of them.
- **Dead standalone pages:** `app/room/[roomId]/3d` (mock data, no inbound links), `app/chatbot`, `app/product-panel`, `app/budget` (real budget is step-5). **Dead component** `components/RoomChatbot.tsx` (contract-mismatched with the streaming `/api/chat`; use `components/chat/ChatPanel.tsx`).
- **Store name collision:** `lib/store/room.ts` and `lib/stores/roomStore.ts` both export `useRoomStore` with different shapes; the `lib/store/` one is live, the `lib/stores/` one is dead. And `useArea2Store` (live) lives in the "legacy" `lib/stores/` folder.
- **Two PostHog inits disagree on host** (`app.posthog.com` vs `eu.posthog.com`). **Two rate limiters** (Upstash current vs in-memory `lib/rateLimit.ts` legacy) plus a DB RPC. **Chat model IDs inconsistent** (`claude-sonnet-4-20250514` vs the non-standard `claude-sonnet-4-6`).
- **`lib/types/supabase.ts` is STALE** — it still lists dropped tables/columns and the old `render_cost_log` shape; regenerate before trusting it, or read the migrations.
- **pgvector has no live ANN index** on `products_v1` (dropped); the vector-search RPCs run unindexed. Product matching now uses SERP live-matching (`room_products.serp_*`), not the CLIP catalog.
- **`products_v1`, not `products`, is the live catalog** — the rename happened directly in the DB; both tables exist in introspection.
- **Input-image size P0:** phone photos (8–15 MB) exceed Claude Vision's 5 MB base64 limit — `normalizeInputImage` (≤1536px, JPEG q85) must run before Modal/Claude calls; `rooms/capture` also EXIF-auto-rotates via `sharp`.
- **Email is off by default** (`EMAIL_SENDING_ENABLED` unset → synthetic success + admin auto-verify at signup). **No Apple OAuth.**
- **Production logger is a full no-op** — don't expect `CovaLogger` output in prod; real prod signal is Sentry + `render_cost_log` + PostHog.

---

## Where to go

- **Current user journey / a specific screen** → `apps/web/app/design/step-*/page.tsx` (order enforced in `apps/web/middleware.ts`); landing `app/page.tsx`; dashboard `app/dashboard/*`.
- **The live redesign orchestration** → `app/api/pipeline/redesign/route.ts` + `lib/ai/design-director.ts` + `lib/ai/prompt-compiler.ts` + Modal `modal_pipeline/redesign.py` + `lib/ai/redesign-client.ts`.
- **Empty-room / furniture erase** → `app/api/render/empty-room/route.ts` + `lib/render/empty-room-v3.ts` + Modal `modal_pipeline/empty_room.py` / `empty_room_gemini.py`.
- **Legacy (v2) render** → `app/api/render/route.ts` + `app/api/render/status/route.ts` + `lib/ai/flux.ts`.
- **Which pipeline a user is on** → `lib/features.ts` (`getRenderPipelineVersion`), `feature_flags` table, `lib/feature-flags/index.ts`.
- **LoRA / style-blend math** → `lib/quiz/style-archetypes.ts` (`fingerprintToStyleBlend`), `lib/styles/lora-registry.ts`, `lib/ai/lora-blending.ts`, `lib/ai/lora-registry.ts`, `style_loras` table.
- **Quiz scoring** → `app/api/quiz/fingerprint/route.ts`, `lib/quiz/bayes.ts`, `app/api/quiz/next-pair/route.ts`; images in `quiz_anchor_images`/`quiz_comparison_pairs`; swipes in `swipe_history`.
- **Detection / masks** → `lib/ai/detection/*` (v2), `app/api/pipeline/{pre-detect,segment-masks}/route.ts` + Modal `perception.py` + `lib/ai/perception-client.ts` (v3).
- **Product matching / commerce** → `app/api/match/route.ts` (heavy), `app/api/pipeline/furniture-match/route.ts` (live), `lib/ai/serper.ts`, `lib/ai/matchUtils.ts`, `lib/affiliates.ts`; results in `room_products`.
- **Chat** → product chatbot `app/api/chat/route.ts` + `components/chat/ChatPanel.tsx`; Area-2 tab chat `app/api/design-chat/route.ts` + `lib/ai/chat/*`.
- **Auth / legal / deletion** → `middleware.ts`, `app/api/auth/*`, `lib/auth/*`, `lib/rate-limit/upstash.ts`, `lib/email/resend.ts`, `account_deletions` table, `delete-user-cascade` Edge Function.
- **Data model truth** → `supabase/migrations/` (read in order; the `20260502203*`/`20260503*` batch is newest) — NOT `lib/types/supabase.ts`.
- **Cost / analytics / flags / config** → `lib/cost/*` + `lib/paid/call.ts` + `render_cost_log`; `lib/analytics*`; `lib/features.ts`/`lib/feature-flags`; `lib/config/timeouts.ts`; `lib/render-config.ts`; `lib/env.ts`.
- **Current build state / roadmap** → `cova-plan/PHASE_STATUS.md` (freshest); `docs/audit/*` for per-domain audits. Ignore `ARCHITECTURE.md`, `docs/STATUS.md`, `COVA_CONTEXT.md` except as historical context.
- **Dead code (don't read to learn the system)** → `_archive/`, `apps/mobile/`, `supabase/functions/` (frozen), `app/design/_future/`, `app/room/[roomId]/3d`, `lib/routes.ts`, `lib/stores/{userStore,roomStore}.ts`, `components/RoomChatbot.tsx`, most of `modal_pipeline/` except the three canonical services.

---

# Navigation map — cova_clone
(Where to GO at the area/module level — not a code index. The exact file:line is looked up live; this is the geography.)

## Where things live (top-level areas)
- .agents/
- .claude/ — 3 mapped source files
- .cursor/
- .expo/
- .github/
- .husky/
- .turbo/
- .vercel/
- _archive/ — 73 mapped source files
- apps/ — 29 mapped source files
- assets/
- bench/ — 24 mapped source files
- benchmarks/
- cova-plan/
- docs/
- ios/ — 3 mapped source files
- modal_pipeline/ — 16 mapped source files
- packages/
- results/
- scripts/ — 33 mapped source files
- styles/
- supabase/
- test-assets/
- test-results/
- tests/ — 12 mapped source files
- .cursorignore
- .gitignore
- .npmrc
- ARCHITECTURE.md
- CHAT_STARTER.md
- CLAUDE.md
- COVA_CONTEXT.md
- COVA_MVP_SPEC_v1.1.docx
- COVA_MVP_SPEC_v1.1.md
- DESIGN.md
- FINAL_AUDIT_REPORT.md
- PRODUCT_TRUTH.md
- README.md
- SCRAPING_LOG.md
- SESSION_0_AUDIT.md
- SITE.md
- SPRINT_LOG.md
- check_fp.mjs
- check_pairs.mjs
- eas.json
- generate_anchors.mjs
- generate_quiz_images.py
- maestro-device.sh
- maestro-sim.sh
- package.json
- pnpm-lock.yaml
- pnpm-workspace.yaml
- pyproject.toml
- quiz_image_prompts.csv
- resume_quiz_images.py
- test_flythrough_verify.json
- test_quiz_combos.mjs
- test_quiz_realistic.mjs
- test_quiz_v2.mjs
- test_quiz_v3.mjs
- test_quiz_v4.mjs
- tsconfig.json
- turbo.json
- vercel.json

## Entry points (highest-rank domain symbols — a starting hint, not exhaustive)
- `modal_pipeline/lib/pipeline_logger.py:75` — info
- `modal_pipeline/utils.py:38` — normalize_label_key
- `modal_pipeline/empty_room_v3.py:301` — upload_image_to_fal
- `generate_anchors.mjs:106` — getFilename
- `apps/web/eslint-rules/no-unsafe-console.js:15` — create
- `modal_pipeline/empty_room.py:813` — load
- `modal_pipeline/embed_catalog_gpu.py:158` — main
- `modal_pipeline/redesign.py:1584` — load
- `modal_pipeline/scrape_catalog.py:832` — main
- `modal_pipeline/perception.py:402` — load
- `modal_pipeline/embed_fusion_gpu.py:147` — main
- `modal_pipeline/postprocess.py:37` — denoise_architecture_region
