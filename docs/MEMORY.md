# Agritech — Session Context Memory

> Saved: 2026-09-17 06:35 IST | Branch: main | Working tree: clean
> Purpose: Persist P0–P6 overhaul context for resume
> Updated: 2026-10-08 — auth-layer hardening (see "2026-10-08 Auth Hardening")

## Objective
Complete the P0–P6 Agritech overhaul: real auth + ownership enforcement, Agmarknet market/price PDF data surfaced in a new market page + charts, seller studio + analytics overhaul, top segmented tabs replacing sidebar nav, and final verification (P6).

## Current State: ALL P0–P6 COMPLETE ✅
All todos completed. Backend auth + market data verified, frontend gate/tabs/market page done, studio/analytics bound to auth, tests green, boot smoke passed.

## Important Details (Decisions & Schema)
- **Auth:** demo/app auth — PBKDF2-SHA256 hashing + MongoDB-backed bearer tokens (`sessions` collection). 401 no/invalid token; 403 wrong role OR wrong owner. Documented in `AUTH.md` (ignored via .gitignore, dev-only). Production notes: httpOnly cookies, short-lived tokens, expiry GC, rate limiting, TLS.
- **Password hash format:** `pbkdf2$sha256$240000$salt_hex$digest_hex`, 240k iterations
- **Sessions:** token=secrets.token_urlsafe(32); stores sha256 hash, expires_at=+1day, is_active
- **Ownership model:** `/api/analytics`, `/api/seller/orders`, `/api/seller/inventory` self-bound + legacy aliases `<id>` return 403 unless id == token user. `POST /api/streams`/end seller-bound+owner; `POST /api/products` seller; `POST /api/orders` buyer (also aliased by `/api/transactions`); `send_stream_message` any authed user (user_id = token). Market ingest sellers-only; market query public.
- **Market schema:** `market_prices` docs: `commodity, commodity_group, market, market_date (ISO), price_per_kg, msp_per_kg, arrival_metric_tonnes, synthetic, source, report_generated_at, raw_text, parse_error, _id(int), created_at`; ₹/quintal→₹/kg ÷100; arrival MT; trend/vs_msp_pct computed in API; unique index `(commodity, market, market_date)`; real beats synthetic.
- **Real PDF format (confirmed via pdfplumber):** header `Marketwise Price & Arrival Report (DD-MM-YYYY)`; `Generated on: DD-MM-YYYY HH:MM AM/PM`; cols = Commodity Group | Commodity | MSP (Rs./Quintal) | Price on D,D-1,D-2 | Arrival on D,D-1,D-2; trailing `Source : https://agmarknet.gov.in/home`; `-` = missing. 2 PDFs → 72 + 69 records (3 days each).
- **MongoDB:** real MongoDB at localhost:27017 (health shows backend `mongodb`); detection via `repr(client)` containing "mongomock"
- **Seed/demo creds:** every seeded user gets `password_hash("demo-password-123")`; sellers ids 1-3 (ramesh_organic_farms, karnataka_produce_hub, punjab_grain_corp), buyers 4-8. `scripts/backfill_auth.py` patches existing DBs.
- **Streamlit:** 1.63.0 — st.segmented_control, st.fragment, st.navigation(position="hidden"), chat_message, badge all available.
- **Acceptance gates:** chat ≤3s no click (st.fragment run_every=3 + ttl=2), is_host=True + host branding, 401/403 ownership, 0 duplicate market rows, 59+ existing tests green + new tests, backend+frontend boot smoke.

## Database Helpers (database/db.py:1)
Implemented fresh: `hash_password`, `verify_password`, `authenticate_user`, `create_login_session`, `_hash_token`, `resolve_session`, `revoke_session`; `sessions` collection + unique `token_hash` index in `_ensure_collections`. Also added `market_prices` + `market_ingest_log` collections + indexes.

## Key Files Modified
- `database/models.py` — `make_chat_message_doc` + `chat_message_to_dict` include `is_host`; `make_user_doc` has `password_hash`
- `database/db.py` — full auth suite + sessions + market collections
- `api/socketio_events.py` — handshake auth (`auth: {token}` → `_AUTHENTICATED_SIDS`; anonymous sockets may watch only); `handle_send_message` takes identity from the sid, payload `user_id`/`is_host` ignored; `build_stream_message_data(stream_id, user_id, message_text)` derives `is_host` from stream ownership + role
- `api/routes.py` — auth endpoints (`/api/auth/*`), `require_auth(role)` decorator, ownership on streams/products/orders/analytics/seller routes incl. self-bound aliases; `POST /api/orders` fix: `def create_order(user=None)` with fallback; `POST /api/transactions` now secured with `@require_auth("buyer")`
- `market_data/parser.py` — `parse_pdf(path, source=None)` with pdfplumber on-demand, header aliases, ParseResult
- `market_data/service.py` — ingest_records dedup logic
- `api/templates/dashboard.html` + `api/static/dashboard.js` — legacy dashboard auth wired
- `seed_data.py` — hashes demo password + truthful backend print
- `scripts/backfill_auth.py` — safe by default: dry-run, requires `--allow-demo-password-reset --confirm` to apply; skips existing hashes; warns prod
- `AUTH.md` — endpoints, ownership table, demo creds, session tokens, hardening notes (gitignored)
- `frontend/app.py` — login gate (Sign in / Create account segmented_control), top tab bar via `st.segmented_control`, role-filtered pages, logout; fixed: `st.line_chart(df.set_index("date"))` (removed invalid `y_labels`)
- `app_pages/api_helpers.py` — token-aware `_api_get`/`_api_post`/`_api_upload` (now surfaces HTTP error bodies, uuid boundary), `current_user()`
- `app_pages/market_prices.py` — new page: commodity price/arrival series, vs MSP, upload via file_uploader
- `app_pages/marketplace.py` — token-bound buyer identity, fragment chat
- `app_pages/seller_studio.py` — stream create reads `resp["data"]` directly, `st.fragment(run_every=3)` + ttl=2, is_host branding, auth binding
- `app_pages/analytics.py` — self-bound via token
- `.gitignore` — completed: Python, Streamlit secrets, venv, coverage, Node/React future (per diverge.md), OS, IDE, Docker, market PDFs
- `tests/test_api.py` — 25 passed after fixes (was 22, now 25 with transactions fix)

## 2026-10-08 Auth Hardening
- **`tests/conftest.py` (new):** pytest swaps `Config.MONGO_DB` to `agritech_test` and drops it at session end. Test `drop_all()` no longer wipes the live `agritech` DB — that was the root cause of "auth broken" (seeded users deleted, then never re-seeded).
- **`main.py`:** auto-seed now triggers when `users == 0` **or** `users-with-password == 0` (the old `count == 0` guard was defeated by a leftover passwordless `legacy-user`).
- **Login:** identifier lowercased when it contains `@`; `database/db.py::authenticate_user` falls back to a case-insensitive username/email match, but only when exactly one account matches.
- **Now guarded:** `GET /api/users` (`@require_auth()`), `POST /api/products/<id>/reviews` (identity = token; body `user_id`/`buyer_name` ignored), `POST /api/products/<id>/price/apply` (`@require_auth("seller")` + owner → 403), Socket.IO `send_message` (handshake token; `_resolve_buyer` removed).
- **Frontend:** `app_pages/api_helpers.py::fetch_me()` validates the stored token on every `frontend/app.py` boot (backend unreachable → `st.stop()`, 401 → clear session + warning, else refresh `auth_user`). Demo captions in `AUTH.md`/`frontend/app.py` corrected to the real seeded accounts.

## Test Results (Last Run)
```
python -m pytest tests -q  ->  80 passed, 7 warnings   (isolated test DB)
Live end-to-end (running server): 32/32 checks — demo logins, mixed-case
  identifier, /api/users 401, review/price 401+403+ownership, is_host
  derivation, Socket.IO handshake (anonymous/valid/revoked), logout 401
Market ingest (earlier): PDF1 72 ins, PDF2 23 ins/46 skip, re-ingest 0/72 skip; 95 real rows, latest 2026-09-13
```

## .gitignore Completion (2026-09-17)
Completed with sections: Streamlit secrets, Env & config, Python, venv, testing/coverage, Jupyter, Node/Frontend (React SPA per diverge.md), OS, IDE (.idea/.vscode), local DBs, AUTH.md (dev-only), Streamlit caches, logs, market PDFs, Docker. Added `.DS_Store`, `coverage.xml`, `htmlcov/`, `node_modules/`, `apps/web/dist/`, `.coverage`, etc.

## Pending / Next Steps
None — all P0–P6 done. If resuming:
1. No file edits needed; backend + frontend ready to `python main.py` + `streamlit run frontend/app.py`
2. Demo login: `ramesh_organic_farms` / `demo-password-123` (seller) or `priya_sharma` / `demo-password-123` (buyer)
3. Future: React SPA migration per `diverge.md` Phases P0–P7 (FastAPI AI service, Mongoose schemas, JWT auth, Socket.IO)

## Notes for Resume
- Do not re-introduce `y_labels` to `st.line_chart`
- Do not add auth bypass to `/api/transactions`
- `scripts/backfill_auth.py` defaults to dry-run; never run blind on prod
- Real MongoDB required; mongomock fallback only if MONGO_URI empty
- Working tree clean; origin/main up-to-date

## 2026-10-27 Repo Cleanup
- Removed dead/duplicate code: `frontend/app_pages/` (superseded duplicate of root `app_pages/`), `frontend/__init__.py`, `frontend/.streamlit/config.toml` (stale copy of root theme), `database/repositories.py` (broken `_next_int_id` import, referenced nowhere). NOTE: `optimization/engine.py` is NOT dead — `api/routes.py`, `optimization/__init__.py` and tests still import it alongside the split modules.
- Removed junk: `node_modules/` + `package.json`/`package-lock.json` (empty `agv` dep, no JS code in repo), root `test_login.py`/`test_pw.py`/`check_users.py` (one-off debug scripts), `scripts/__load_agmarknet_tmp.py` (one-off loader, superseded by `scripts/load_agmarknet_data.py`), legacy local sqlite DBs `data/agri_sales.db`/`data/agritech.db` (project is MongoDB-only), duplicate root PDFs (fixtures live in `data/samples/`), all `__pycache__/` + `.pytest_cache/`.
- Restructured docs into `docs/`: `docs/diverge.md` (blueprint, moved), `docs/AUTH.md` (dev-only, moved), `docs/MEMORY.md` (this file, moved). `.gitignore` cleaned (dead `scripts/__load_agmarknet_tmp.py` entry + broken absolute Temp path removed; `AUTH.md` pattern already matches in any folder).

## 2026-10-27 Auth Layer Removed
The authentication/authorization layer was stripped in full per user request. Semantics now:
- **No login exists anywhere.** All endpoints are public. Buyer-side actions act as the *first seeded buyer* (`priya_sharma`), seller-side actions as the *first seeded seller* (`ramesh_organic_farms`) — resolved per-request in `api/routes.py` (`_demo_seller` / `_demo_buyer`).
- **Removed:** `/api/auth/*` endpoints, `require_auth`, all ownership/role checks, `hash_password`/`verify_password`/`authenticate_user`/`create_login_session`/`resolve_session`/`revoke_session` + `sessions` collection (database/db.py), `password_hash` from `make_user_doc`, Socket.IO handshake auth + `_AUTHENTICATED_SIDS`, Streamlit login gate + token plumbing in `app_pages/api_helpers.py` (`_ensure_identities`/`current_user`(buyer)/`current_seller` now), legacy dashboard login overlay + auth in `api/static/dashboard.js`, `scripts/backfill_auth.py`, `tests/test_backfill_auth.py`, `tests/test_socketio_auth.py`, `docs/AUTH.md`.
- **Chat identity:** REST `POST /api/streams/send_message` — payload `is_host: true` → sender is the stream's seller (host badge derived server-side as before); payload `user_id` honoured if it exists; default = demo buyer. Socket.IO `send_message` resolves `user_id` or demo buyer.
- **Order/review identity:** demo buyer by default; review honours an explicit valid `user_id`.
- **main.py auto-seed guard simplified** to `users == 0` (the old "no usable password logins" check no longer makes sense and would have wiped data every boot).
- **Frontend:** all five tabs shown for everyone; Streamlit `frontend/app.py` pins demo identities from `/api/users`; `_api_get` cache key no longer includes a token.
- **Tests:** `tests/test_api.py` rewritten (seeds seller+buyer directly, no 401/403 cases); conftest (isolated `agritech_test` DB) unchanged.

## 2026-10-27 Slow Boot Fixed (NLP Flags Enforced)
First-boot seeding hung for minutes and the user Ctrl-C'd mid-run — root cause: `nlp_engine.py` never checked `Config.USE_TRANSFORMER_SENTIMENT` (it existed, default off, and `.env` set it `false`, but was dead code), so `analyze_sentiment` (distilbert) and especially `detect_intent` (`facebook/bart-large-mnli` zero-shot, very slow on CPU) ran unconditionally for the ~50 sentiment + 30 intent seeding calls on every empty-DB bootstrap.
- **Fix:** `_get_transformer_sentiment` now gated by `Config.USE_TRANSFORMER_SENTIMENT` and `_get_transformer_intent` by a new `Config.USE_TRANSFORMER_INTENT` (both default **false**). Keyword matcher for intent + VADER for sentiment are the default paths (verified equivalent results on the seeded intents: PRICE/QUALITY/DELIVERY inquiries all resolve correctly). Transformers remain opt-in via env flags; model loading is still lazy and alive when a flag is enabled (`.env.example` documents `USE_TRANSFORMER_INTENT=false`).
- **Measured:** `import main` (auto-seed) dropped to ~12s from multi-minute hangs.
- **Important operational note:** when MongoDB (`localhost:27017`) is not running, the app silently falls back to in-process `mongomock` — seed data does not persist and every boot re-seeds. Start MongoDB first (`.env` points at `mongodb://localhost:27017`); `net start MongoDB` requires an elevated shell.
