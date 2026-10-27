# Computational Optimization Framework for Agricultural Sales — Production Blueprint

Upgrade path: **Streamlit prototype → production-grade microservices architecture** for the
*"Computational Optimization Framework for Agricultural Sales Leveraging Live-Streaming
E-Commerce and Semantic NLP Techniques"* major project.

Current verified core (never to be rewritten): `nlp/` (spaCy + VADER + Transformers,
`analyze_sentiment`, `detect_intent`, `embed_text`, `text_similarity`) and `optimization/`
(`DynamicPricingEngine`, `RecommendationEngine`, `DemandForecaster`, `metrics`).

---

## 1. ARCHITECTURAL TOPOLOGY & COMMUNICATION PROTOCOL

### 1.1 System topology

```
                         ┌──────────────────────────────────────────────────┐
                         │                     CLIENT (SPA)                 │
                         │  React 19 · Vite · TypeScript · Tailwind CSS      │
                         │  Zustand (state) · React Router · Recharts         │
                         │  Socket.io-client · Lucide Icons                  │
                         └───────────────┬───────────────────┬──────────────┘
                              HTTPS REST │                   │ WSS (Socket.io)
                           /api/v1/* JSON│                   │ /socket.io
                         ┌───────────────▼───────────────────▼──────────────┐
                         │             API GATEWAY  (Node 20 + Express)     │
                         │   Auth/JWT · RBAC · rate limiting                │
                         │   Mongoose (Mongo access) · sanitization · helmet │
                         │   Socket.IO server (HTTP upgrade, same origin)     │
                         └───────┬──────────────────────┬───────────────────┘
                                 │                      │  internal HTTP JSON
                                 │  CRUD + aggregation  │  (localhost / AI subnet)
                                 ▼                      ▼
                         ┌───────────────┐   ┌───────────────────────────────┐
                         │     MongoDB   │   │   AI/ML MICROSERVICE (Python)  │
                         │  Replica set  │   │   FastAPI · spaCy · VADER      │
                         │  (primary WD) │   │   Transformers · scikit-learn  │
                         └───────────────┘   └───────────────────────────────┘
```

Rationale: the AI calls are coarse-grained, latency-tolerant units (sentiment per chat
message, pricing per request, forecast/recommendation on demand). REST + JSON is the
simplest contract that load-balances behind a reverse proxy, survives warm AI-worker
restarts, and gives OpenAPI documentation from FastAPI for free. Reserve gRPC only for a
future high-frequency stream-analytics pipeline.

### 1.2 Node ⇄ Python AI contract (mirrors the verified engine signatures exactly)

The gateway never calls spaCy/sklearn directly — it proxies the `body` to the AI service
over HTTP. Every route maps 1:1 to an existing tested function:

| # | Method  | Node route                        | AI service endpoint     | Reuses (existing, tested)                             |
|---|---------|-----------------------------------|-------------------------|-------------------------------------------------------|
| 1 | POST    | `/api/v1/ai/stream/analyze`       | `POST /ai/stream/analyze`| `nlp.analyze_sentiment` + `nlp.detect_intent` (the `build_stream_message_data` pair) |
| 2 | POST    | `/api/v1/ai/sentiment`            | `POST /ai/sentiment`    | `nlp.analyze_sentiment`                               |
| 3 | POST    | `/api/v1/ai/intent`               | `POST /ai/intent`       | `nlp.detect_intent`                                   |
| 4 | POST    | `/api/v1/ai/similarity`           | `POST /ai/similarity`   | `nlp.compute_semantic_similarity`, `embed_text`       |
| 5 | POST    | `/api/v1/ai/pricing/suggest`      | `POST /ai/pricing`      | `DynamicPricingEngine.suggest_price` (full factor breakdown) |
| 6 | POST    | `/api/v1/ai/recommend`            | `POST /ai/recommend`    | `RecommendationEngine.fit + get_recommendations`, `build_engine_from_products` |
| 7 | POST    | `/api/v1/ai/forecast`             | `POST /ai/forecast`     | `DemandForecaster.predict_demand`, `forecast_demand`  |
| 8 | POST    | `/api/v1/ai/metrics/demand`       | `POST /ai/demand`       | `metrics.demand_score`                                |
| 9 | POST    | `/api/v1/ai/metrics/summary`      | `POST /ai/summary`      | `sales_summary`, `revenue_by_day`, `rating_summary`   |
| 10| GET     | `/api/v1/ai/health`               | `GET /health`           | service liveness/readiness probes                     |

**Example contract — real-time chat message (hottest path):**

```jsonc
// Gateway → AI  POST /ai/stream/analyze
{ "message_text": "Tomato price kya hai?" }

// AI → Gateway  200
{
  "sentiment_score": -0.31,
  "sentiment_label": "NEGATIVE",      // POSITIVE | NEUTRAL | NEGATIVE
  "intent_tag": "PRICE_INQUIRY"       // PRICE_INQUIRY | QUALITY_INQUIRY | DELIVERY_INQUIRY | GENERAL_CHAT
}
```

**Example contract — dynamic pricing (suggest, don't force):**

```jsonc
// Gateway → AI  POST /ai/pricing
{
  "base_price": 40.0,
  "demand_score": 0.83,               // from metrics.demand_score over orders
  "stock_level": 240.0,
  "competitor_min": 34.0, "competitor_max": 55.0,   // optional
  "reference_price": 42.0             // optional; enables the margin path
}

// AI → Gateway  200
{
  "base_price": 40.0, "suggested_price": 43.52,
  "change_pct": 0.088, "demand_factor": 1.0825, "stock_factor": 1.0325,
  "competitor_adjustment": 0.0, "reason": "baseline recommendation"
}
```

**Example contract — demand forecast:**

```jsonc
// Gateway → AI  POST /ai/forecast
{ "history": [ {"date": "2026-09-01", "quantity": 120}, { "date": "2026-09-02", "quantity": 140} ],
  "horizon": 7 }

// AI → Gateway  200
{
  "predictions": [ { "date": "2026-09-10", "predicted_quantity": 133.4 } ],
  "trend_slope": 1.8,
  "model_used": "linear-regression-ensemble"
}
```

**Fault / timing / idempotency policy**
- AI service returns `503 {"error":"model unavailable"}` when heavy deps (Transformers/GPU)
  fail to load. Gateway falls back to the last cached value or the lexicon-only (VADER) path —
  never blocks the chat.
- Node sets `X-Request-Id` + `X-AI-Timeout: 2000`, retries failed POSTs once, and treats the
  socket write as committed *after* AI success (same ordering as today: NLP → persist → broadcast).
- Caching: identical `message_text` sentiment/intent cached in a gateway in-process LRU
  (TTL 60s); pricing/forecast cached per product (TTL 5m), invalidated on order-create.

---

## 2. MONGODB SCHEMAS & ORM DESIGN (Mongoose)

Field names and enums mirror `database/models.py` exactly so existing seed/tests/analytics
port with zero data cleanup. `_id` is the Mongoose ObjectId; a `legacyId` int field is
preserved only during Phase-2 dual-write, then dropped.

```ts
// User — gains password/auth; role enum matches UserRole (seller|buyer)
const userSchema = new Schema({
  username:  { type: String, required: true, trim: true, maxlength: 80, index: true },
  email:     { type: String, required: true, unique: true, lowercase: true, index: true },
  password:  { type: String, required: true, minlength: 8, select: false }, // bcrypt hash
  role:      { type: String, enum: ["seller","buyer"], default: "buyer", index: true },
  location:  { type: String, trim: true, maxlength: 120 },
  refreshToken: String,                // hashed, single-active-session
}, { timestamps: true, minimize: false });
userSchema.index({ role: 1, location: 1 });
```

```ts
// Product — text index for search + category filter
const productSchema = new Schema({
  sellerId:   { type: Schema.Types.ObjectId, ref: "User", required: true, index: true },
  name:       { type: String, required: true, trim: true, maxlength: 160 },
  category:   { type: String, enum: ["Grain","Vegetable","Fruit","Organic"], default: "Vegetable", index: true },
  description:{ type: String, trim: true, default: "" },
  price:      { type: Number, min: 0, required: true, index: true },
  stockKg:    { type: Number, min: 0, default: 0 },
  rating:     { type: Number, default: 0 },
}, { timestamps: true });
productSchema.index({ name: "text", description: "text" });   // MongoDB $text search
productSchema.index({ sellerId: 1, category: 1 });            // seller inventory scan
```

```ts
// LiveStream
const liveStreamSchema = new Schema({
  sellerId:     { type: Schema.Types.ObjectId, ref: "User", required: true, index: true },
  streamTitle:  { type: String, required: true, trim: true, maxlength: 200 },
  isActive:     { type: Boolean, default: true, index: true },
  startedAt:    { type: Date, default: Date.now },
  endedAt:      Date,                        // populated by /streams/<id>/end
}, { timestamps: true });
liveStreamSchema.index({ isActive: 1, startedAt: -1 });   // hot "live now" feed
```

```ts
// ChatMessage — the realtime NLP output document
const chatMessageSchema = new Schema({
  streamId:       { type: Schema.Types.ObjectId, ref: "LiveStream", required: true, index: true },
  userId:         { type: Schema.Types.ObjectId, ref: "User", required: true, index: true },
  messageText:    { type: String, required: true, trim: true, maxlength: 500 },
  sentimentScore: { type: Number, min: -1, max: 1, default: 0 },
  sentimentLabel: { type: String, enum: ["POSITIVE","NEUTRAL","NEGATIVE"], default: "NEUTRAL", index: true },
  intentTag:      { type: String, enum: ["PRICE_INQUIRY","QUALITY_INQUIRY","DELIVERY_INQUIRY","GENERAL_CHAT"], default: "GENERAL_CHAT", index: true },
}, { timestamps: true });
chatMessageSchema.index({ streamId: 1, createdAt: -1 });              // recent-messages fetch
chatMessageSchema.index({ streamId: 1, sentimentLabel: 1, intentTag: 1 }); // live sentiment distribution
```

```ts
// Review
const reviewSchema = new Schema({
  productId:      { type: Schema.Types.ObjectId, ref: "Product", required: true, index: true },
  userId:         { type: Schema.Types.ObjectId, ref: "User", required: true, index: true },
  reviewText:     { type: String, trim: true, default: "" },
  rating:         { type: Number, min: 0, max: 5, required: true },
  sentimentScore: { type: Number, min: -1, max: 1, default: 0 },
}, { timestamps: true });
reviewSchema.index({ productId: 1, createdAt: -1 });   // reviews-listing + rating recompute
// unique compound index (productId, userId) prevents duplicate reviews — enforced in bootstrap
```

```ts
// Order
const orderSchema = new Schema({
  buyerId:    { type: Schema.Types.ObjectId, ref: "User", required: true, index: true },
  productId:  { type: Schema.Types.ObjectId, ref: "Product", required: true, index: true },
  quantityKg: { type: Number, min: 0.001, required: true },
  totalPrice: { type: Number, min: 0, required: true },
  status:     { type: String, enum: ["pending","confirmed","shipped","delivered","cancelled"], default: "pending", index: true },
}, { timestamps: true });
orderSchema.index({ productId: 1, createdAt: -1 });   // analytics + forecast inputs
orderSchema.index({ buyerId: 1, status: 1 });         // "my orders"
orderSchema.index({ productId: 1, status: 1 });       // demand_score aggregation
```

```ts
// PriceLog — audit trail for every applied price
const priceLogSchema = new Schema({
  productId: { type: Schema.Types.ObjectId, ref: "Product", required: true, index: true },
  oldPrice:  { type: Number, min: 0 },
  newPrice:  { type: Number, min: 0 },
  reason:    { type: String, default: "" },
  appliedBy: { type: Schema.Types.ObjectId, ref: "User", index: true },  // audit (who)
}, { timestamps: true });
priceLogSchema.index({ productId: 1, createdAt: 1 });
```

Notes:
- `Schema.Types.ObjectId` refs give relational integrity.
- Unique compound index on `reviews (productId, userId)` prevents duplicate reviews.
- Run `createIndexes()` in an idempotent bootstrap step.
- All `timestamps` are UTC ISO — `metrics.revenue_by_day` ports over unchanged.

---

## 3. VITAL AUTH & SECURITY MIDDLEWARE (Express)

```ts
// src/middleware/auth.ts
import jwt, { type SignOptions } from "jsonwebtoken";
import bcrypt from "bcryptjs";

export const signToken = async (userId: string, role: string, ttl: string, secret: string) =>
  jwt.sign({ sub: userId, role }, secret, { expiresIn: ttl, issuer: "agritech" } as SignOptions);

export const protect = async (req, res, next) => {          // (a) JWT auth
  const header = req.headers.authorization || "";
  if (!header.startsWith("Bearer ")) return res.status(401).json({ error: "Not logged in" });
  const token = header.split(" ")[1];
  try {
    const payload = jwt.verify(token, process.env.JWT_ACCESS_SECRET!, { issuer: "agritech" });
    req.user = await User.findById(payload.sub).select("+password").exec();
    if (!req.user) return res.status(401).json({ error: "User no longer exists" });
    req.userId = payload.sub;
    next();
  } catch { return res.status(401).json({ error: "Invalid or expired token" }); }
};

export const restrictTo = (...roles: string[]) =>            // (b) RBAC
  (req, res, next) =>
    roles.includes(req.user?.role)
      ? next()
      : res.status(403).json({ error: "You do not have permission" });
// Usage: router.post("/products", protect, restrictTo("seller"), createProduct);
```

```ts
// (c) Password hashing + session lifecycle
export const hashPassword = (plain: string) => bcrypt.hash(plain, 12);
export const comparePassword = (plain: string, hash: string) => bcrypt.compare(plain, hash);

export const login = async (email, password) => {
  const user = await User.findOne({ email }).select("+password").exec();
  if (!user || !(await comparePassword(password, user.password!)))
    throw { status: 401, message: "Invalid credentials" };
  const accessToken  = await signToken(user.id, user.role, "15m", process.env.JWT_ACCESS_SECRET!);
  const refreshToken = await signToken(user.id, user.role, "7d", process.env.JWT_REFRESH_SECRET!);
  user.refreshToken = await hashPassword(refreshToken);      // store hashed, single-session
  await user.save();
  return { user, accessToken, refreshToken };                 // access → memory, refresh → HttpOnly cookie
};
// /auth/refresh: verify refresh JWT, compare to stored hash, rotate, issue new pair.
// /auth/logout:  clear refreshToken + HttpOnly cookie (enables revoke-by-session).
```

Global middleware chain:

```ts
// src/server.ts
app.use(helmet());                                   // security headers
app.use(cors({ origin: process.env.WEB_ORIGIN, credentials: true })); // SPA :5173, API :3000
app.use(express.json({ limit: "10kb" }));
app.use("/api/v1/auth", rateLimit({ windowMs: 60_000, limit: 10 }));     // brute force
app.use("/api/v1",     rateLimit({ windowMs: 60_000, limit: 120 }));
app.use("/api/v1/ai",  internalIpOnly, aiTimeout);    // AI subnet ACL + 2s timeout
```

Security completeness checklist:
- `helmet` + CORS with `credentials`.
- bcrypt cost 12; JWT access 15m in memory (Zustand), refresh 7d in secure HttpOnly cookie
  (`sameSite: lax`; `secure` off for local HTTP demo).
- Mongo injection neutralized by Mongoose; payload size caps.
- Enforce roles server-side — never trust client `user_id` (today's biggest gap).
- Socket.IO handshake guarded with the access token (`io.use(protectSocket)`), so
  `send_message` can no longer impersonate a buyer.

---

## 4. PHASED MIGRATION ROADMAP (zero breakage of verified Python logic)

| Phase | Scope | Deliverable | Exit criteria |
|-------|-------|-------------|---------------|
| **P0** | Freeze AI core as importable package | Repo layout `apps/web`, `apps/api-gateway`, `services/ai-engine` COPYING `nlp/` + `optimization/` as-is; `pyproject.toml` pins | `pytest 48 passed` still green in the AI service |
| **P1** | Wrap AI core in FastAPI | `/ai/{sentiment,intent,similarity,pricing,recommend,forecast,demand,summary,stream/analyze,health}` — thin JSON adapters, **zero algorithm changes** | Contract tests hit every engine path; OpenAPI generated |
| **P2** | Mongoose data plane (parallel write) | `services/api-gateway` with Mongoose, 7 schemas, dual-write + backfill seed (reuse `seed_data.py` → JSON dump → mgmt seed script) | Doc-count parity between legacy Mongo and new Mongo collections |
| **P3** | Auth + RBAC + audit | `protect`, `restrictTo`, bcrypt flow, refresh rotation, `priceLogs.appliedBy` | Negative tests: no-token / expired / role-403 all pass |
| **P4** | Business + AI orchestration routes | 26 legacy endpoints re-exposed as `/api/v1/*`; aggregation via Mongoose `$match/$group`; orchestrator routes call AI service | Old and new gateway return identical JSON shapes for the same seed |
| **P5** | Real-time layer | Socket.IO server (namespaces `streams`, auth middleware, rooms `stream_<id>`, `join_stream/leave_stream/send_message/get_stream_state`); Node calls `POST /ai/stream/analyze` exactly like `build_stream_message_data` today | Loaded chat browser shows badges in <800ms |
| **P6** | React SPA | Vite + TS + Tailwind + Zustand + React Router; `socket.io-client`; Recharts analytics; screens cover every `/api/v1/*` route; **Streamlit killed last** | Lighthouse ≥85; zero console errors |
| **P7** | Hardening & DX | Docker compose (mongo, gateway, ai-engine, web nginx), `.env` templates, CI (lint→test→build), Pino logging, `/healthz` probes, README runbook | `docker compose up` boots full stack from clean clone |

Non-breaking guarantees:
- The legacy Flask service stays up and served at `:5000` until P6 feature parity, behind
  per-endpoint feature flags `V1`/`V2`.
- Both gateways exercise the AI contract against the *same* `nlp/` + `optimization/` package.
- Streamlit can be pointed at either gateway without code change during transition.
- Known dead code NOT migrated: `database/repositories.py` (bad `_next_int_id` import),
  duplicated `frontend/app_pages/`, and `optimization/engine.py` vs the split
  `pricing/recommendation/forecasting` modules — keep only `optimization/{pricing,recommendation,forecasting,metrics}`.

---

## 5. RESUME IMPACT & TECH STACK HIGHLIGHTS (SDE bullets)

- **Engineered a full-stack microservices marketplace** for agricultural e-commerce — React
  (Vite + TS + Tailwind + Zustand) SPA, Express API gateway with JWT auth, RBAC
  (`seller/buyer`), and rate limiting, with a decoupled Python AI microservice, reducing
  chat-message NLP round-trips by exposing a single `/ai/stream/analyze` contract.
- **Designed production MongoDB (Mongoose) schemas** across 7 domain entities with
  text-search and aggregation indexes and relational refs, plus audited `PriceLog` trails —
  preserving feature parity across a phased 8-stage migration.
- **Built real-time features on Socket.IO/WebSockets** — per-stream rooms, JWT-guarded
  handshakes, and live sentiment/intent badges persisted from a shared NLP pipeline (spaCy,
  VADER, Transformers), powering a per-stream sentiment & intent dashboard.
- **Owned the semantic-NLP + optimization layer** — dynamic pricing
  (`DynamicPricingEngine`), content-based recommendations (TF-IDF + cosine), and demand
  forecasting (linear-regression ensemble), all behind a versioned, OpenAPI-documented REST
  contract with graceful model-unavailable fallbacks.

#Tags: `React 19 · Node/Express · FastAPI · MongoDB/Mongoose · Socket.IO · spaCy · scikit-learn · Docker`

---

## 6. DECISIONS LOG & LOCAL RUNBOOK

### Decisions log
| Decision | Choice | Rationale |
|----------|--------|-----------|
| AI service stack | **FastAPI** | Async, auto OpenAPI docs, Pydantic validation — reads best on a resume; wraps existing `nlp/` + `optimization/` unchanged |
| Token storage | **HttpOnly cookie** | Refresh token in secure HttpOnly cookie, access token held in Zustand memory — XSS-safe, industry standard |
| Deployment target | **Local demo only** | Runs on interview machine; optional `docker-compose.yml` for reproducibility |

### Local runbook (3 terminals)
```bash
# Terminal 1 — React SPA (http://localhost:5173)
cd apps/web && pnpm dev

# Terminal 2 — API Gateway (http://localhost:3000)
cd apps/api-gateway && node src/server.js

# Terminal 3 — AI microservice (http://localhost:8000, docs at /docs)
cd services/ai-engine && uvicorn ai.main:app --reload --port 8000
```

Database: `docker run -p 27017:27017 mongo:7` — or use the `mongodb-memory-server`
fallback build so the demo runs with zero external infrastructure.