# Agritech Platform: Redis Integration & Dynamic Pricing Engine Specification

This specification provides the complete technical blueprint for refactoring the Agritech platform from legacy MongoDB/mixed backend dependencies to a high-throughput, real-time Redis + Flask + Streamlit architecture.

---

## 1. System Architecture Overview

```
 ┌─────────────────────────────────────────────────────────┐
 │                  Streamlit UI Frontend                  │
 │          (Fragment Polling @st.fragment(ttl=3))         │
 └───────────────────────────┬─────────────────────────────┘
                             │ HTTP / REST
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │                    Flask REST Engine                    │
 │               (127.0.0.1:5000 Engine/API)               │
 └───────────────────────────┬─────────────────────────────┘
                             │ RESP2 (port 6379)
                             ▼
 ┌─────────────────────────────────────────────────────────┐
 │                   Redis In-Memory Data                  │
 │            (Hashes, Sets, Streams & Pub/Sub)            │
 └─────────────────────────────────────────────────────────┘
```

### Component Boundaries & Network Interfaces
- **Redis Server**: `127.0.0.1:6379` (DB 0, RESP2 Protocol). Stores product state, live streams, chat queues, and dynamic metrics.
- **Flask REST API**: `127.0.0.1:5000`. Handles business logic, mathematical surge pricing calculations, order processing, and VADER sentiment processing.
- **Streamlit App**: `127.0.0.1:8501`. Consumes API endpoints via localized 3-second fragment polling.

---

## 2. Dynamic Pricing Engine Mathematics

The core dynamic pricing engine calculates real-time commodity prices based on baseline APMC market data, inventory scarcity, and buyer chat sentiment.

### 2.1 Baseline Variables & Constraints
For any commodity product $i$:
- $P_{\text{base}}$: Baseline APMC Market Price (`day2_price` in ₹/kg).
- $P_{\text{prev}}$: Yesterday's APMC Market Price (`day1_price` in ₹/kg).
- $P_{\min}$: Floor Safety Limit (₹/kg), where $P_{\min} = 0.85 \times P_{\text{base}}$.
- $P_{\max}$: Ceiling Safety Limit (₹/kg), where $P_{\max} = 1.25 \times P_{\text{base}}$.
- $S$: Current Stock Level (in kg).
- $S_{\text{ref}}$: Reference Nominal Stock Level ($S_{\text{ref}} = 500\text{ kg}$).

---

### 2.2 VADER Chat Sentiment Aggregation
Given a rolling window of $N$ buyer chat messages $M = \{m_1, m_2, \dots, m_N\}$ for product $i$, each message yields a compound VADER score $v(m) \in [-1.0, 1.0]$.

The aggregate sentiment score $V$ is given by:

$$V = \frac{1}{N} \sum_{k=1}^{N} v(m_k)$$

---

### 2.3 Demand Surge Multiplier ($\alpha$)
The demand multiplier $\alpha$ combines normalized inventory scarcity and sentiment intensity:

$$\alpha = \left( \frac{S_{\text{ref}}}{\max(S, 1)} \right)^{\gamma} \times \left( 1 + \beta \cdot V \right)$$

Where parameters are tuned as:
- Sensitivity scaling exponent: $\gamma = 0.15$
- Sentiment influence factor: $\beta = 0.20$

---

### 2.4 Dynamic Price Calculation & Safety Clamping

The unconstrained dynamic price $P_{\text{raw}}$ is computed as:

$$P_{\text{raw}} = P_{\text{base}} \times \alpha$$

Applying safety boundary conditions and manual seller clamping override:

$$P_{\text{dynamic}} = \begin{cases} 
P_{\text{base}} & \text{if } \text{capped} = \text{true} \\
P_{\max} & \text{if } P_{\text{raw}} > P_{\max} \text{ and } \text{capped} = \text{false} \\
P_{\min} & \text{if } P_{\text{raw}} < P_{\min} \text{ and } \text{capped} = \text{false} \\
P_{\text{raw}} & \text{otherwise}
\end{cases}$$

---

### 2.5 Dynamic Price Surge Percentage
The percentage change displayed to end users ($\Delta P_{\%}$) relative to the APMC baseline is calculated as:

$$\Delta P_{\%} = \left( \frac{P_{\text{dynamic}} - P_{\text{base}}}{P_{\text{base}}} \right) \times 100$$

*(Note: The backend supplies pre-scaled values; do not multiply by 100 twice on the frontend).*

---

## 3. Redis Data Schema Specifications

### 3.1 Hash Key: `product:<id>`
Stores individual APMC commodity attributes.

| Key Field | Data Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `id` | String | `"1"` | Unique product identifier |
| `name` | String | `"Onion - Local"` | Display name of commodity |
| `day1_price` | Float (String) | `"35.00"` | Yesterday APMC baseline price |
| `day2_price` | Float (String) | `"44.00"` | Today APMC baseline price |
| `dynamic_price` | Float (String) | `"48.50"` | Calculated live dynamic price |
| `max_price` | Float (String) | `"55.00"` | Dynamic price ceiling bound |
| `min_price` | Float (String) | `"37.40"` | Dynamic price floor bound |
| `stock_kg` | Float (String) | `"350.0"` | Inventory level remaining in kg |
| `capped` | String | `"false"` | Dynamic clamp status (`"true"` / `"false"`) |

### 3.2 Set Key: `streams:all`
Contains active stream IDs (e.g., `{"1", "2"}`).

### 3.3 Hash Key: `stream:<id>`
Stores metadata for live auction channels.
- `id`: `"1"`
- `title`: `"Bangalore APMC Live Auction"`
- `status`: `"active"`

### 3.4 List Key: `stream:<id>:chats`
JSON stringified list of chat payloads:
```json
{"id": "c1", "product_id": "1", "message": "Need 500kg Onion immediately!", "sentiment": 0.62, "timestamp": 1798403000}
```

---

## 4. Phase Migration Directives for OpenCode

### Phase 1: Environment & Port Sanitization
- **Enforce Port Isolation**: Ensure Flask binds strictly to `127.0.0.1:5000` and Redis binds strictly to `127.0.0.1:6379`.
- **Eliminate Redis-as-HTTP Calls**: Verify that all `api_helpers.py` requests target `http://127.0.0.1:5000`.

### Phase 2: Python Path & Type Safety Compliance
- **Path Bootstrap**: Insert at top of all files in `app_pages/` and `frontend/`:
  ```python
  import sys
  from pathlib import Path
  _REPO_ROOT = Path(__file__).resolve().parents[1]
  if str(_REPO_ROOT) not in sys.path:
      sys.path.insert(0, str(_REPO_ROOT))
  ```
- **Pylance Type Safety**:
  - Class `C` must explicitly declare `API_BASE_URL: str = "http://127.0.0.1:5000"`.
  - Signature `_api_get` must return `Optional[Dict[str, Any]]`.
  - Use `typing.cast(BadgeColor, ...)` for Streamlit `st.badge` color parameters.

### Phase 3: REST API Implementation (`main.py`)
Implement the following standard routes in Flask:
1. `GET /health` $\rightarrow$ `{"status": "ok", "db": true}`
2. `GET /api/products` $\rightarrow$ Returns array of all 31 commodity hashes.
3. `GET /api/streams` $\rightarrow$ Returns list of active streams (with default fallback `[{"id": "1", "title": "Bangalore APMC Live Auction", "status": "active"}]`).
4. `POST /api/streams/chats` $\rightarrow$ Receives `{"stream_id": str, "product_id": str, "message": str}`, runs VADER sentiment, recalculates $P_{\text{dynamic}}$, and updates Redis.
5. `POST /api/orders` $\rightarrow$ Receives `{"product_id": str, "quantity_kg": float}`, performs atomic `HINCRBYFLOAT` stock deduction on `stock_kg`, and returns confirmation.

### Phase 4: Streamlit Real-Time Fragment UI Refactor
- Wrap product rendering inside `@st.fragment(run_every=3)`.
- Render a 4-column layout for each product:
  - **Col 1**: APMC Baseline (`day2_price`) & trend vs `day1_price`.
  - **Col 2**: Dynamic Price (`dynamic_price`) & surge percentage $\Delta P_{\%}$.
  - **Col 3**: Stock level (`stock_kg`) with `st.warning` if $< 50\text{ kg}$.
  - **Col 4**: Clamped status badge with explicit `BadgeColor` typing.

---

## 5. Seeder Regex Parser (`database/pdf_seeder.py`)

OpenCode must use this regex pattern to parse pipe-delimited vertical PDF tables:

```python
import re
import redis
from pypdf import PdfReader

def parse_and_seed_pdf(pdf_path: str):
    reader = PdfReader(pdf_path)
    text = "\n".join([page.extract_text() for page in reader.pages])
    
    pattern = r"([A-Za-z0-9\s\-\(\)\/\,]+)\n\s*\|\s*([\d\,\.]+)\n\s*\|\s*([\d\,\.]+)\n\s*\|\s*([\d\,\.]+)\n\s*\|\s*([\d\,\.]+)\n\s*\|\s*([\d\,\.]+)\n\s*\|\s*(\d{4}\-\d{2}\-\d{2})"
    matches = re.findall(pattern, text)
    
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    count = 0
    
    for match in matches:
        name = match[0].strip()
        if "Commodity" in name or "1KG Price" in name:
            continue
        
        try:
            price_1kg = float(match[1].replace(',', ''))
            max_q = float(match[3].replace(',', ''))
            min_q = float(match[4].replace(',', ''))
            prev_price = float(match[5].replace(',', ''))
            
            count += 1
            r.hset(f"product:{count}", mapping={
                "id": str(count),
                "name": name,
                "day1_price": str(prev_price),
                "day2_price": str(price_1kg),
                "dynamic_price": str(price_1kg),
                "max_price": str(max_q / 100.0),
                "min_price": str(min_q / 100.0),
                "stock_kg": "500.0",
                "capped": "false"
            })
        except ValueError:
            continue
            
    # Seed default stream fallback
    r.hset("stream:1", mapping={"id": "1", "title": "Bangalore APMC Live Auction", "status": "active"})
    r.sadd("streams:all", "1")
    print(f"Successfully seeded {count} products and stream defaults into Redis.")
```

---

## 6. Execution Verification Protocol

OpenCode should verify system health by executing the following commands sequentially:

```powershell
# 1. Re-seed database from PDF
.venv\Scripts\python.exe database\pdf_seeder.py data\bengaluru_apmc_day2.pdf

# 2. Check Redis Ping and Keys
.venv\Scripts\python.exe -c "import redis; r = redis.Redis(host='127.0.0.1', port=6379, db=0); print('Redis Ping:', r.ping()); print('Product Keys:', len(r.keys('product:*')))"

# 3. Start Flask REST Engine (Terminal 1)
.venv\Scripts\python.exe main.py

# 4. Verify API Endpoints via PowerShell
Invoke-RestMethod -Uri "http://127.0.0.1:5000/health"
Invoke-RestMethod -Uri "http://127.0.0.1:5000/api/products"

# 5. Launch Streamlit UI (Terminal 2)
.venv\Scripts\streamlit.exe run app_pages/seller_studio.py
```