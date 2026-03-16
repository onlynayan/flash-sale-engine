# ⚡ Flash Sale Engine — Complete Architecture Guide & Tutorial

A production-grade, high-performance Flash Sale backend built with **FastAPI**, **Redis**, **Oracle Database**, and a real-time **WebSocket** frontend. This document is a complete walkthrough of every concept, design decision, and data flow — written to serve as both a technical reference and a learning tutorial for beginners.

---

## 📑 Table of Contents

1. [Project Overview](#1-project-overview)
2. [The Problem We Are Solving](#2-the-problem-we-are-solving)
3. [Tech Stack](#3-tech-stack)
4. [File Structure Explained](#4-file-structure-explained)
5. [Core Concept 1 — ORM & Database Models](#5-core-concept-1--orm--database-models)
6. [Core Concept 2 — FastAPI & HTTP Requests](#6-core-concept-2--fastapi--http-requests)
7. [Core Concept 3 — Redis as a Cache Server & Bouncer](#7-core-concept-3--redis-as-a-cache-server--bouncer)
8. [Core Concept 4 — Redis Streams (Message Queue)](#8-core-concept-4--redis-streams-message-queue)
9. [Core Concept 5 — The Background Worker](#9-core-concept-5--the-background-worker)
10. [Core Concept 6 — Pessimistic Locking](#10-core-concept-6--pessimistic-locking)
11. [Core Concept 7 — Reservation Timers (Watchdog Pattern)](#11-core-concept-7--reservation-timers-watchdog-pattern)
12. [Core Concept 8 — WebSockets & Real-Time UI](#12-core-concept-8--websockets--real-time-ui)
13. [Core Concept 9 — JSON Payloads](#13-core-concept-9--json-payloads)
14. [Core Concept 10 — The Shopping Cart System](#14-core-concept-10--the-shopping-cart-system)
15. [Core Concept 11 — Server-Side Pagination](#15-core-concept-11--server-side-pagination)
16. [Core Concept 12 — Frontend JavaScript Manipulation](#16-core-concept-12--frontend-javascript-manipulation)
17. [Complete User Journey — Step by Step](#17-complete-user-journey--step-by-step)
18. [Full Architecture Flowchart](#18-full-architecture-flowchart)
19. [How to Run the Project](#19-how-to-run-the-project)
20. [Environment Variables](#20-environment-variables)

---

## 1. Project Overview

The **Flash Sale Engine** simulates a real-world, high-traffic e-commerce flash sale — the kind run by Amazon, Flipkart, and Shopee — where thousands of users simultaneously fight over limited stock. It demonstrates that a naive "read stock → decrement stock → write order" approach will **fail catastrophically** under load, and then systematically solves that problem with industry-standard patterns.

**Key features:**
- ⚡ **Redis Bouncer** — rejects 99% of requests before they touch the database
- 📬 **Redis Streams** — decouples order acceptance from database writes
- 🔒 **Pessimistic Locking** — prevents race conditions during Oracle writes
- ⏱️ **Reservation Timers** — auto-releases inventory if payment stalls
- 🛒 **Shopping Cart with Global Timer** — Ticketmaster-style bulk checkout
- 📡 **WebSockets** — pushes live stock updates to every open browser tab
- 📄 **Server-Side Pagination** — efficient product loading from the database

---

## 2. The Problem We Are Solving

### The Naive Approach (and why it breaks)

Imagine 1,000 users all try to buy the last iPhone at the exact same millisecond. A naive server would:

```
1. User 1 reads stock → 1 (OK!)
2. User 2 reads stock → 1 (OK!)
   ... (997 more users also read 1)
3. User 1 writes: stock = stock - 1 = 0 ✅
4. User 2 writes: stock = stock - 1 = 0 ✅
   ... (997 more users also write 0)
```

**Result: 1,000 orders placed for 1 item.** This is an **oversell bug** — the classic nightmare of flash sale systems.

**Secondary problem:** Even if you use database locks to prevent oversell, your database gets hammered with 1,000 simultaneous connections, causing it to slow down or crash entirely.

**Our solution** is a layered defense:

```
Internet → FastAPI → Redis Bouncer → Redis Stream → Worker → Oracle DB
                         ↑                              ↑
                 (Atomic counter)              (Pessimistic lock)
```

Each layer handles a different concern. Read on to understand each one.

---

## 3. Tech Stack

| Layer | Technology | Role |
|---|---|---|
| **API Server** | FastAPI (Python) | Handles HTTP and WebSocket connections |
| **Cache / Bouncer** | Redis (Upstash) | Atomic stock counter, cart storage, pub/sub |
| **Message Queue** | Redis Streams | Reliable, persistent order queue |
| **Permanent Database** | Oracle DB | Source of truth for users, products, orders |
| **ORM** | SQLAlchemy (Async) | Translates Python objects to SQL queries |
| **Frontend** | Vanilla HTML/CSS/JS | Real-time UI rendered in the browser |
| **Real-Time Push** | WebSockets | Live stock updates from server to all browsers |
| **Schema Validation** | Pydantic | Validates all incoming API request data |
| **Config** | python-dotenv | Loads secrets from `.env` file |

---

## 4. File Structure Explained

```
flash-sale-engine/
│
├── main.py          # 🧠 The brain: All FastAPI routes, Redis logic, timers,
│                   #    AND the embedded order worker (runs as asyncio task)
├── worker.py        # 👷 Optional standalone worker: use this to run the consumer
│                   #    as a completely separate process for horizontal scaling
├── database.py      # 🗄️ Database engine setup and session factory
├── models.py        # 📐 SQLAlchemy table definitions (ORM models)
├── schemas.py       # ✅ Pydantic request/response validation schemas
├── sync_all.py      # 🔄 Admin script: syncs Oracle stock → Redis on startup
│
├── templates/
│   └── index.html   # 🖥️ The HTML shell of the entire frontend
│
├── static/
│   ├── styles.css   # 🎨 All visual styling rules
│   └── app.js       # ⚙️ All client-side logic (cart, WebSocket, pagination)
│
├── .env             # 🔑 Secret keys (Redis URL, DB URL) — never commit this!
├── .env.example     # 📋 Template showing which variables are needed
├── requirements.txt # 📦 All Python dependencies
└── README.md        # 📖 This file
```

---

## 5. Core Concept 1 — ORM & Database Models

### What is an ORM?

**ORM** stands for **Object-Relational Mapper**. It is a programming technique that lets you work with a **database table as if it were a Python class**, and a **database row as if it were a Python object**.

Without ORM, you write raw SQL:
```sql
INSERT INTO products (name, price, stock) VALUES ('iPhone', 120000, 10);
```

With ORM (SQLAlchemy), you write Python:
```python
product = Product(name="iPhone", price=120000, stock=10)
db.add(product)
await db.commit()  # SQLAlchemy generates the SQL for you!
```

### Our Database Tables (`models.py`)

We define 3 tables as Python classes:

```python
class User(Base):
    __tablename__ = "users2"
    id       = Column(Integer, Identity(start=1), primary_key=True)
    username = Column(String(100), unique=True)

class Product(Base):
    __tablename__ = "products"
    id    = Column(Integer, Identity(start=1), primary_key=True)
    name  = Column(String(100))
    price = Column(Integer)   # Stored in CENTS to avoid floating-point errors!
    stock = Column(Integer)

class Order(Base):
    __tablename__ = "orders"
    id         = Column(Integer, Identity(start=1), primary_key=True)
    user_id    = Column(Integer, ForeignKey("users2.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    created_at = Column(DateTime, default=func.now())
```

> **Why store price in cents?** `$39.99` represented as a float is actually `39.98999999...` in binary. Money calculations with floats lead to off-by-one cent errors across millions of transactions. Storing as `3999` (integer cents) is always exact.

### `database.py` — The Connection Pool

```python
engine = create_async_engine(DATABASE_URL, echo=True)
```

The `engine` is a **connection pool** — a manager that keeps a set of pre-opened database connections ready so our API doesn't waste time establishing a new TCP connection on every request. When a request comes in, it borrows a connection from the pool, uses it, and returns it.

`AsyncSession` means all database I/O is **non-blocking**. While Oracle is executing a slow query, Python can handle other incoming requests instead of freezing.

---

## 6. Core Concept 2 — FastAPI & HTTP Requests

### What is FastAPI?

**FastAPI** is a modern Python web framework. It listens on a port (e.g., `8000`), receives HTTP requests from browsers or apps, runs your Python code, and sends back HTTP responses.

### HTTP Methods

Your browser uses different HTTP verbs for different actions:

| Method | Used For | Example |
|---|---|---|
| `GET` | Read data | Load the product list |
| `POST` | Create / trigger action | Place an order, add to cart |

### Our API Routes at a glance

```
GET  /display       → Serves you the HTML page (index.html)
GET  /products/     → Returns paginated product list JSON
POST /cart/add      → Add one item to the cart
POST /cart/remove   → Remove one item from the cart
POST /cart/checkout → Send the entire cart to the worker
POST /orders/       → (Legacy) Single-item order endpoint
WS   /ws/catalog    → WebSocket for real-time stock pushes
```

### Pydantic — Request Validation (`schemas.py`)

When a browser sends a POST request with a JSON body, FastAPI uses **Pydantic** to automatically validate it. If a required field is missing or has the wrong type, FastAPI immediately returns a `422 Unprocessable Entity` error **before your function code even runs**.

```python
# schemas.py
class CartAdd(BaseModel):
    user_id: int              # Must be an integer
    product_id: int           # Must be an integer
    cart_id: Optional[str] = None  # Can be null (first item in cart)
```

---

## 7. Core Concept 3 — Redis as a Cache Server & Bouncer

### What is Redis?

**Redis** (Remote Dictionary Server) is an **in-memory key-value store**. Think of it as an extraordinarily fast Python dictionary that is shared between all your server processes and persists across restarts.

| Speed | Oracle DB | Redis |
|---|---|---|
| Typical query | 5–50ms | < 0.2ms |
| Concurrent users | ~100–500 | ~100,000+ |

This 100x speed difference is why Redis exists.

### Redis as a Cache

A **cache** is a fast, temporary copy of slow data. We use Redis to store the live stock count for every product. When a user loads the product page, we read stock from Redis (fast) instead of Oracle (slow), and display that number.

```
Redis Key: "stock:14"
Redis Value: "8"
```

When the Oracle database changes stock, we update the Redis value too. This keeps the cache in sync. The script `sync_all.py` batch-syncs all Oracle stock values into Redis on startup.

### Redis as the Bouncer

Here is the core insight of the entire system: **we do NOT ask Oracle if stock is available. We ask Redis.**

When a user clicks "Add to Cart", `main.py` runs:

```python
new_stock = await redis_client.decr(f"stock:{product_id}")
```

`DECR` is a Redis command. It:
1. Reads the current integer value
2. Subtracts 1 from it
3. Writes the new value back
4. **All three steps happen atomically** — no other process can interfere between them

This is the key safety guarantee! Because `DECR` is atomic, it is **physically impossible** for two simultaneous requests to both decrement from `1` to `0` — one will get `0` (allowed), and the other will get `-1` (rejected).

```python
if new_stock < 0:
    # Undo the decrement — we rejected this user
    await redis_client.incr(f"stock:{product_id}")
    raise HTTPException(status_code=400, detail="Out of stock!")
```

The Bouncer rejects all invalid requests instantly (< 1ms) without touching Oracle at all.

```
10,000 requests → Redis Bouncer → 9,990 REJECTED immediately
                                →      10 ALLOWED through to the queue
```

---

## 8. Core Concept 4 — Redis Streams (Message Queue)

### Why do we need a Message Queue?

After the Bouncer approves a user, we STILL don't write to Oracle immediately. Why? Because Oracle writes are slow and can fail. If we write synchronously (within the HTTP request), we make the user wait, and if Oracle is down, the order is lost.

Instead, we use the **Producer-Consumer pattern**:

- **Producer** → `main.py` (FastAPI): Receives the request and puts a message on the queue.
- **Consumer** → `worker.py`: Independently reads from the queue and writes to Oracle at its own pace.

The user gets a fast response ("Order placed!") immediately, while the worker processes it in the background.

### What is a Redis Stream?

A **Redis Stream** is a persistent, append-only log of messages — like a conveyor belt. Producers put items on one end; consumers pick them up from the other end. Crucially, messages **stay on the belt** (unacknowledged) until the consumer explicitly confirms they processed them. This gives us reliability: if the worker crashes mid-process, the message is retried.

```python
# main.py — Producer
payload = json.dumps({"order_id": order_id, "user_id": 1, "items": [14, 7]})
await redis_client.xadd("order_stream", {"payload": payload})
```

```python
# worker.py — Consumer
result = await redis_client.xreadgroup(GROUP_NAME, CONSUMER_NAME, {"order_stream": ">"}, count=1, block=0)
message_id = result[0][1][0][0]
payload_str = result[0][1][0][1]["payload"]
```

After processing:
```python
await redis_client.xack("order_stream", GROUP_NAME, message_id)
# Now the message is removed from the "pending" state
```

### Consumer Groups

We use a **Consumer Group** so that multiple worker instances can share the same stream without processing the same message twice. Redis delivers each message to only one consumer in the group.

---

## 9. Core Concept 5 — The Background Worker

The project supports **two deployment modes** for the order consumer:

### Mode A — Embedded Worker (Default, Recommended for single-server)

The `process_orders()` coroutine runs **inside `main.py`** as an `asyncio.create_task()`. It starts automatically when FastAPI starts and shares the same process.

```python
# In main.py lifespan startup:
worker_task = asyncio.create_task(process_orders())
```

**Advantage:** Only ONE command needed to run the entire system.
**Trade-off:** If the API process dies, the worker dies too.

### Mode B — Standalone Worker (`worker.py`) for Horizontal Scaling

For production at scale (e.g., high-traffic events), you can run `worker.py` as a completely separate process — or even on a separate server entirely. Multiple worker instances can all consume from the same Redis Stream simultaneously, and Redis ensures **each message is delivered to only one worker** (via Consumer Groups).

```
 API Server × 2          Worker Process × 5
[main.py]  [main.py]    [worker.py] [worker.py] [worker.py]
     ↓           ↓            ↓           ↓           ↓
          Redis Stream (order_stream)
                         ↓
                    Oracle Database
```

### Two Types of Payloads the Worker Handles

**Type 1: Old single-item order (legacy)**
```json
{"order_id": "abc-123", "user_id": 1, "product_id": 14}
```

**Type 2: New bulk cart checkout**
```json
{"order_id": "cart-uuid", "user_id": 1, "items": ["14", "7"]}
```

The worker distinguishes them by checking if the `"items"` key exists:
```python
items = order_data.get("items")
if items is not None:
    # Handle bulk cart checkout...
else:
    product_id = order_data.get("product_id")
    # Handle single order...
```

---

## 10. Core Concept 6 — Pessimistic Locking

### The Problem Without Locking

Even inside the worker, we can have race conditions if two workers run simultaneously. Imagine:

```
Worker A reads: Product 14 has stock = 1. OK!
Worker B reads: Product 14 has stock = 1. OK!
Worker A writes: stock = 0, creates Order A ✅
Worker B writes: stock = -1, creates Order B ✅ (OVERSELL!)
```

### The Solution: `SELECT ... FOR UPDATE`

**Pessimistic Locking** means: "I don't trust that the data won't change. I'm going to lock the row before I read it so nobody else can touch it until I'm done."

```python
# worker.py
query = select(models.Product).where(models.Product.id == product_id).with_for_update()
```

The `.with_for_update()` tells Oracle to place a **row-level lock** on that product row. Any other transaction that tries to SELECT the same row with `FOR UPDATE` will **block and wait** until we release the lock with a `COMMIT`.

This guarantees our check-then-decrement is atomic at the database level too.

```
Worker A: SELECT ... FOR UPDATE → Lock acquired, stock = 1
Worker B: SELECT ... FOR UPDATE → BLOCKED (waiting for Worker A)
Worker A: stock -= 1, INSERT order, COMMIT → Lock released
Worker B: Lock acquired, stock = 0 → REJECT (no stock)
```

Why is this called "pessimistic"? Because we assume the worst — we assume someone WILL try to steal the resource from us. The opposite, **Optimistic Locking**, assumes nobody will interfere and only checks for conflicts at commit time.

---

## 11. Core Concept 7 — Reservation Timers (Watchdog Pattern)

### The Problem

After the Redis Bouncer approves a user, stock is decremented in Redis. But the Oracle write is pending in the queue. What if the worker crashes and never processes the message? The stock is "gone" in Redis but no order exists in Oracle. That inventory is permanently lost.

### The Watchdog

When an order is approved in `main.py`, we immediately create a **reservation key** in Redis and launch an **asyncio background task** (the watchdog):

```python
order_id = str(uuid.uuid4())
await redis_client.set(f"reservation:{order_id}", "reserved")
asyncio.create_task(reservation_rollback_timer(order_id, user_id, product_id))
```

The watchdog timer:
1. Sleeps for 30 seconds.
2. Wakes up and checks: **does the reservation key still exist?**
3. The worker, upon successfully processing the order, **deletes the reservation key** to signal "I handled this."
4. **If the key still exists** after 30 seconds, it means the worker failed. The watchdog restores the stock to Redis and broadcasts the update to all UIs.

```python
async def reservation_rollback_timer(order_id, user_id, product_id):
    await asyncio.sleep(30)
    exists = await redis_client.exists(f"reservation:{order_id}")
    if exists:
        # Worker failed! Restore the stock
        await redis_client.delete(f"reservation:{order_id}")
        new_stock = await redis_client.incr(f"stock:{product_id}")
        await redis_client.publish("stock_updates", json.dumps({...}))
```

### Cart Watchdog (40 seconds)

For the shopping cart, we use the same pattern — a `bulk_reservation_rollback_timer` that:
- Starts when the **first** item is added to the cart
- Does **not reset** when more items are added
- After 40 seconds, if the cart has not been checked out, it restores ALL items in the cart list back to Redis stock

This is the **Ticketmaster model**: once your timer starts, you have a fixed window to complete payment. Adds do not extend the deadline.

---

## 12. Core Concept 8 — WebSockets & Real-Time UI

### HTTP vs WebSocket

| HTTP | WebSocket |
|---|---|
| Client asks → Server answers → Connection closed | Connection stays open permanently |
| Server can ONLY speak when spoken to | Server can push data to client at any time |
| Good for loading pages | Good for chat, live data, stock tickers |

When your browser opens the Flash Sale page, `app.js` immediately establishes a WebSocket connection:

```javascript
ws = new WebSocket(`ws://127.0.0.1:8000/ws/catalog`);
ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    document.getElementById(`stock-display-${data.product_id}`).innerText = data.stock;
};
```

The server keeps a list of all connected browsers:
```python
active_connections: list[WebSocket] = []
```

Whenever stock changes (from an order, cart add, or refund), `main.py` publishes to Redis Pub/Sub:
```python
await redis_client.publish("stock_updates", json.dumps({"product_id": 14, "stock": 7}))
```

A background listener (`redis_pubsub_listener`) receives that from Redis and broadcasts it to every single connected WebSocket client:
```python
for connection in active_connections:
    await connection.send_text(data)
```

**Result:** Every open browser tab updates the stock number simultaneously without polling, without refreshing.

### Why Two Redis Clients?

```python
redis_client        # For COMMANDS: DECR, GET, SET, XADD, etc.
redis_pubsub_client # For SUBSCRIBE: Listening for published messages
```

In Redis, once a connection enters `SUBSCRIBE` mode, it can no longer send regular commands. That's why we maintain a dedicated second connection for pub/sub.

---

## 13. Core Concept 9 — JSON Payloads

**JSON** (JavaScript Object Notation) is the universal data format for web APIs. It is the "language" that the frontend and backend use to talk to each other.

Every time the browser sends data to FastAPI or FastAPI sends data to the Worker, it sends a structured JSON string.

### Example 1 — Add to Cart Request (Browser → FastAPI)

```json
{
  "user_id": 1,
  "product_id": 14,
  "cart_id": "a3b8d1b6-0b3b-4b1a-9c1a-1a2b3c4d5e6f"
}
```

### Example 2 — Bulk Checkout Payload (FastAPI → Redis Stream → Worker)

```json
{
  "order_id": "a3b8d1b6-0b3b-4b1a-9c1a-1a2b3c4d5e6f",
  "user_id": 1,
  "items": ["14", "7", "22"]
}
```

### Example 3 — Stock Update (Redis Pub/Sub → WebSocket → Browser)

```json
{
  "product_id": 14,
  "stock": 7
}
```

FastAPI uses Python dictionaries for this, and `json.dumps()` / `json.loads()` to serialize/deserialize:

```python
import json
payload = json.dumps({"product_id": 14, "stock": 7})  # Dict → JSON string
data = json.loads(payload_str)                         # JSON string → Dict
product_id = data["product_id"]                        # Access like a dict
```

---

## 14. Core Concept 10 — The Shopping Cart System

### Architecture

The cart system implements the **Global Cart Timer** pattern, inspired by Ticketmaster's ticket checkout flow.

**Key rules:**
1. The timer starts with the **first item** added
2. The timer **does NOT reset** when more items are added
3. All items in the cart are **reserved in Redis** — their stock is already decremented
4. If the timer expires, **ALL items are automatically refunded** to Redis stock
5. Checkout sends a single bulk payload to the worker, which processes all items in one database transaction

### Data Storage in Redis

When items are added to a cart, they are stored in a Redis List:

```
Key: "cart:{cart_id}:items"
Value: ["14", "7", "22"]   ← A Redis List (rpush appends to the end)
```

### Race Condition Prevention

A subtle bug exists if two items are clicked simultaneously: both HTTP requests fire before either gets a response, so both carry `cart_id = null` and the server creates **two separate carts** for the same user.

**Fix:** The browser generates the `cart_id` itself **synchronously** using `crypto.randomUUID()` the instant the first click fires — before any network call happens:

```javascript
if (!cartId) {
    cartId = crypto.randomUUID(); // Generated instantly, synchronously!
}
// All parallel inflight requests now share the exact same cartId
```

The server uses `redis_client.llen()` to check if the cart list is empty instead of relying on whether the client passed `null`:
```python
existing_items = await redis_client.llen(cart_items_key)
is_first_item = (existing_items == 0)
if is_first_item:
    asyncio.create_task(bulk_reservation_rollback_timer(cart_id))
```

### Complete Cart Flow

```
1. User clicks "Add to Cart" on Keyboard ($40)
   → Browser generates cart_id = "abc-123" instantly
   → POST /cart/add  {user_id:1, product_id:14, cart_id:"abc-123"}
   → FastAPI: Redis DECR stock:14 (9→8) ✅
   → FastAPI: Redis RPUSH cart:abc-123:items "14"
   → FastAPI: Launches 40s watchdog for cart "abc-123"
   → Response: {cart_id:"abc-123"}
   → Browser: Timer starts, cart drawer opens, "Keyboard" shows in cart

2. User clicks "Add to Cart" on RAM ($120)
   → cart_id = "abc-123" (already set)
   → POST /cart/add  {user_id:1, product_id:7, cart_id:"abc-123"}
   → FastAPI: Redis DECR stock:7 (6→5) ✅
   → FastAPI: Redis RPUSH cart:abc-123:items "7"
   → FastAPI: llen = 1 (not first item), NO new watchdog started
   → Browser: RAM added to cart drawer

3. Timer reaches 0 OR user clicks "Complete Payment"
   → POST /cart/checkout  {user_id:1, cart_id:"abc-123"}
   → FastAPI: checks cart exists in Redis ✅
   → FastAPI: items = LRANGE cart:abc-123:items = ["14","7"]
   → FastAPI: XADD order_stream payload={order_id, user_id, items}
   → Response: "Checkout successful!"
   → Browser: cart cleared, drawer closed, success popup

4. Worker (background process) picks up from stream
   → Checks: does "cart:abc-123:items" still exist? ✅
   → Opens Oracle transaction (pessimistic lock per product)
   → For product 14: stock -= 1, INSERT order
   → For product 7:  stock -= 1, INSERT order
   → COMMIT (both writes atomic)
   → XACK message_id (remove from pending)
   → DELETE cart:abc-123:items (signal: watchdog, don't refund)
```

---

## 15. Core Concept 11 — Server-Side Pagination

### Why Not Load Everything?

If the catalog has 10,000 products, sending all 10,000 in one HTTP response would:
- Take many seconds to transfer
- Use enormous amounts of RAM in the browser
- Slow down Oracle with a massive sequential scan

Instead, we use **server-side pagination**: the client requests a specific "page" of results.

### How It Works

The browser requests:
```
GET /products/?skip=0&limit=12    → Page 1 (products 1-12)
GET /products/?skip=12&limit=12   → Page 2 (products 13-24)
GET /products/?skip=24&limit=12   → Page 3 (products 25-36)
```

FastAPI uses SQLAlchemy's `.offset()` and `.limit()`:
```python
result = await db.execute(select(models.Product).offset(skip).limit(limit))
```

It also counts the total to calculate the total number of pages:
```python
count_query = await db.execute(select(func.count()).select_from(models.Product))
total_count = count_query.scalar()
total_pages = math.ceil(total_count / limit)
```

The response includes both the products AND pagination metadata:
```json
{
  "items": [{...}, {...}, ...],
  "total": 100,
  "page": 1,
  "pages": 9
}
```

The frontend uses `data.pages` and `data.page` to render the navigation buttons.

---

## 16. Core Concept 12 — Frontend JavaScript Manipulation

The frontend is a Single Page Application (SPA) — the HTML page is loaded once, and JavaScript dynamically rewrites the page content without ever doing a full browser refresh.

### Key JavaScript Concepts Used

**1. `fetch()` — Making HTTP requests from JavaScript**
```javascript
const response = await fetch('/cart/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: 1, product_id: 14, cart_id: "abc-123" })
});
const data = await response.json();
```

**2. `async/await` — Not freezing the browser**

`await` pauses the current async function until the network call completes, but the browser remains fully responsive during this time. Without `async/await`, you'd need to use `.then()` callback chains which become hard to read.

**3. DOM Manipulation — Changing page content**
```javascript
// Change a number on screen
document.getElementById("stock-display-14").innerText = 7;

// Add/remove CSS classes to show/hide elements
document.getElementById("cart-drawer").classList.add("open");

// Write entirely new HTML into a container
document.getElementById("catalog").innerHTML = cardHTML;
```

**4. `setInterval` — The countdown timer**
```javascript
timerInterval = setInterval(() => {
    timeRemaining--;
    document.getElementById("time-left").innerText = `00:${timeRemaining}`;
    if (timeRemaining <= 0) clearInterval(timerInterval);
}, 1000); // Fires every 1000ms = 1 second
```

**5. WebSocket — Live updates**
```javascript
ws = new WebSocket("ws://127.0.0.1:8000/ws/catalog");
ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    document.getElementById(`stock-display-${data.product_id}`).innerText = data.stock;
};
```

---

## 17. Complete User Journey — Step by Step

This traces every single event that occurs from the moment a user opens their browser to the moment their order is in Oracle.

```
STEP 1: Browser navigates to http://localhost:8000/display
   └─ GET /display → Jinja2 renders index.html
   └─ Browser loads styles.css and app.js

STEP 2: app.js initializes
   └─ loadCatalog(page=1) called
       └─ GET /products/?skip=0&limit=12
          └─ FastAPI reads products from Oracle (via SQLAlchemy)
          └─ For each product, reads LIVE stock from Redis
          └─ Returns PaginatedProductResponse JSON
       └─ JS renders 12 product cards in the grid
       └─ renderPagination() draws page number buttons
   └─ connectWebSocket() called
       └─ Browser opens persistent WebSocket to /ws/catalog
       └─ FastAPI stores this connection in active_connections[]

STEP 3: User clicks "Add to Cart" on Keyboard
   └─ JS: cartId = crypto.randomUUID() (generated instantly!)
   └─ JS: timerStarted = false (timer not yet confirmed)
   └─ POST /cart/add {user_id:1, product_id:14, cart_id:"abc"}
       └─ Pydantic validates the request body
       └─ Redis: DECR stock:14  (10 → 9)
       └─ Redis: Publish {product_id:14, stock:9} → stock_updates channel
           └─ PubSub listener wakes up → broadcasts to ALL WebSockets
           └─ Every open browser tab instantly shows Keyboard stock = 9
       └─ Redis: LLEN cart:abc:items = 0 → is_first_item = True
       └─ Redis: RPUSH cart:abc:items "14"
       └─ FastAPI: asyncio.create_task(bulk_reservation_rollback_timer("abc"))
           └─ Watchdog starts sleeping for 40 seconds in background...
       └─ Response: {message: "Added", cart_id: "abc"}
   └─ JS receives OK response
   └─ JS: cartItems.push({id:14, name:"Keyboard", price:4000})
   └─ JS: timerStarted = true → startCartTimer() → countdown begins in cart
   └─ JS: cart drawer slides open, badge shows 1

STEP 4: User clicks "Add to Cart" on RAM
   └─ cartId = "abc" (already set, no new UUID)
   └─ POST /cart/add {user_id:1, product_id:7, cart_id:"abc"}
       └─ Redis: DECR stock:7 (6 → 5)
       └─ Redis: Publish → all tabs show RAM stock = 5
       └─ Redis: LLEN cart:abc:items = 1 → is_first_item = False → NO new watchdog!
       └─ Redis: RPUSH cart:abc:items "7"
       └─ Response: {cart_id: "abc"}
   └─ JS: timerStarted = true → timer does NOT restart (already running)
   └─ Cart shows: Keyboard $40 + RAM $120 = TOTAL $160

STEP 5: User clicks "Complete Payment"
   └─ JS: checkoutBtn.innerText = "Processing..."
   └─ JS: clearInterval(timerInterval) (pauses visual countdown)
   └─ POST /cart/checkout {user_id:1, cart_id:"abc"}
       └─ Redis: EXISTS cart:abc:items → Yes ✅ (cart not expired)
       └─ Redis: LRANGE cart:abc:items 0 -1 → ["14", "7"]
       └─ FastAPI builds payload: {order_id:"abc", user_id:1, items:["14","7"]}
       └─ Redis: XADD order_stream {payload: "...json..."}
       └─ Response: "Checkout successful!"
   └─ JS: shows "Order Successful!" popup
   └─ JS: cartItems=[], cartId=null, timerStarted=false, timeRemaining=40
   └─ Cart drawer closes

STEP 6: Worker processes the bulk order (background)
   └─ XREADGROUP returns 1 message
   └─ payload: {order_id:"abc", user_id:1, items:["14","7"]}
   └─ items is not None → bulk cart mode
   └─ Redis: EXISTS cart:abc:items → Yes ✅ (reservation valid)
   └─ async with db.begin():  ← Begin Oracle transaction
       └─ SELECT Product WHERE id=14 FOR UPDATE → lock row, stock=9
          └─ product.stock -= 1 (→ 8), INSERT Order(user_id=1, product_id=14)
       └─ SELECT Product WHERE id=7 FOR UPDATE → lock row, stock=5
          └─ product.stock -= 1 (→ 4), INSERT Order(user_id=1, product_id=7)
       └─ COMMIT ← Both writes land in Oracle atomically
   └─ XACK message_id ← Message removed from "pending" state
   └─ Redis: DELETE cart:abc:items ← Signals watchdog: "I handled it!"

STEP 7: Watchdog timer fires (40s after first cart add)
   └─ Redis: EXISTS cart:abc:items → Key DELETED ✅
   └─ Watchdog sees nothing to do. Exits quietly.

STEP 8: Worker broadcasts stock updates after Oracle write
   └─ (Optional: could publish final stock to Redis Pub/Sub)
   └─ Next time a user loads /products/, they see correct stock from Redis
```

---

## 18. Full Architecture Flowchart

```
                    ┌──────────────────────────────────┐
                    │         BROWSER (User)           │
                    │                                  │
                    │  index.html + styles.css         │
                    │  app.js: fetch(), WebSocket,     │
                    │           setInterval(), DOM     │
                    └────────────┬─────────────────────┘
                                 │
                    HTTP Requests│  WebSocket (persistent)
                    POST, GET    │  ws://...
                                 ▼
                    ┌────────────────────────────────────────┐
                    │            FastAPI (main.py)           │
                    │                                        │
                    │  /display   /products/   /cart/add     │
                    │  /cart/checkout  /cart/remove          │
                    │  /ws/catalog  (WebSocket handler)      │
                    │                                        │
                    │  ┌─────────────────────────────────┐  │
                    │  │   Reservation Watchdog Timers   │  │
                    │  │   (asyncio background tasks)    │  │
                    │  └─────────────────────────────────┘  │
                    └──────────┬──────────────┬─────────────┘
                               │              │
              Reads/Writes     │              │ Publish to
              stock, cart,     │              │ "stock_updates"
              reservations     │              │ channel
                               ▼              ▼
                    ┌─────────────────────────────────────┐
                    │         REDIS (Upstash)             │
                    │                                     │
                    │  stock:{id}          → integer      │
                    │  cart:{id}:items     → List         │
                    │  reservation:{id}    → string       │
                    │  order_stream        → Stream       │
                    │  [PubSub channel]                   │
                    └────────────┬────────────────────────┘
                                 │
                                 │ Subscribe to "stock_updates"
                                 │
                    ┌────────────▼────────────────────────┐
                    │    PubSub Listener (in main.py)     │
                    │    redis_pubsub_listener()          │
                    │    Runs as asyncio.Task             │
                    └────────────┬────────────────────────┘
                                 │
                                 │ Broadcast to all
                                 │ active WebSocket connections
                                 ▼
                    ┌─────────────────────────────────────┐
                    │     All Connected Browser Tabs      │
                    │     Stock numbers update instantly  │
                    └─────────────────────────────────────┘


   SEPARATE PROCESS:
                    ┌─────────────────────────────────────┐
                    │       Worker (worker.py)            │
                    │                                     │
                    │  XREADGROUP order_stream (blocks    │
                    │  until message arrives)             │
                    │                                     │
                    │  Handles:                           │
                    │    - Single orders                  │
                    │    - Bulk cart checkouts            │
                    │                                     │
                    │  Validates reservation key in Redis │
                    │  If valid → write to Oracle DB      │
                    │  XACK → delete reservation key      │
                    └────────────┬────────────────────────┘
                                 │
                    SELECT...    │  INSERT orders
                    FOR UPDATE   │  UPDATE products
                    (Pessimistic │
                     Locking)    │
                                 ▼
                    ┌─────────────────────────────────────┐
                    │        Oracle Database              │
                    │                                     │
                    │  Tables: users2, products, orders   │
                    │  Accessed via SQLAlchemy ORM        │
                    │  (Source of Truth for all data)     │
                    └─────────────────────────────────────┘
```

### Flash Sale Purchase Flow (Condensed Flowchart)

```
User clicks "Add to Cart"
        │
        ▼
[JS] Generate cart_id (if needed)
        │
        ▼
POST /cart/add → FastAPI
        │
        ▼
Redis: DECR stock:{id}
        │
    ┌───┴───┐
  < 0?      ≥ 0?
    │         │
    ▼         ▼
  INCR      RPUSH cart items
  (undo)    Publish stock update
  422       Launch watchdog (if 1st item)
  Error     200 OK
    │         │
    ▼         ▼
[JS] Toast  [JS] Add to cart UI
  error     Start timer (if 1st success)

          [User clicks "Complete Payment"]
                      │
                      ▼
            POST /cart/checkout
                      │
                      ▼
            Redis EXISTS cart? → No → 400 Expired
                      │
                      ▼ Yes
            LRANGE cart items list
                      │
                      ▼
            XADD order_stream {bulk payload}
                      │
                      ▼
            200 OK → Browser clears cart

                [Worker picks up]
                      │
                      ▼
            EXISTS reservation? → No → Discard (expired)
                      │
                      ▼ Yes
            BEGIN TRANSACTION
                      │
                      ▼
            FOR EACH item:
              SELECT...FOR UPDATE
              stock -= 1
              INSERT order
                      │
                      ▼
            COMMIT → XACK → DELETE reservation
```

---

## 19. How to Run the Project

### Prerequisites

- Python 3.11+
- Oracle Database (or Oracle Always Free Cloud)
- [Upstash](https://upstash.com) account for Serverless Redis (free tier available)
- Oracle Instant Client (for `oracledb`)

### Installation

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd flash-sale-engine

# 2. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
source venv/bin/activate # macOS/Linux

# 3. Install all dependencies
pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root (never commit this!):

```env
REDIS_URL=redis://default:<password>@<host>:<port>
DATABASE_URL=oracle+oracledb://<user>:<password>@<host>:<port>/?service_name=<service>
```

### Running

#### ✅ Mode A: Single Process (Recommended — Worker is embedded in main.py)

Just **one command** starts everything: the API, WebSocket listener, watchdog timers, AND the order consumer:

```bash
uvicorn main:app --reload
```

**Optional — Sync Oracle stock to Redis after startup:**
```bash
python sync_all.py
```

Open your browser at: **http://127.0.0.1:8000/display**

---

#### ⚙️ Mode B: Separate Processes (For Horizontal Scaling)

If you want to run multiple worker instances independently (e.g., for load testing or production scaling), use two terminals:

**Terminal 1 — FastAPI server (with embedded worker disabled in `main.py`):**
```bash
uvicorn main:app --reload
```

**Terminal 2 — Standalone worker:**
```bash
python worker.py
```

Both will share the same Redis Stream. Redis distributes messages so each order is processed exactly once.

---

## 20. Environment Variables

| Variable | Description | Example |
|---|---|---|
| `REDIS_URL` | Full connection URL for Upstash Redis | `redis://default:pass@host:6379` |
| `DATABASE_URL` | SQLAlchemy connection URL for Oracle | `oracle+oracledb://user:pass@host/svc` |

> ⚠️ **Security Warning:** Never commit your `.env` file to version control. The `.dockerignore` and `.gitignore` files should both exclude it. Your Redis password and database credentials are sensitive secrets.

---

## Key Takeaways

| Problem | Solution | Where |
|---|---|---|
| Oversell under load | Atomic `DECR` in Redis | `main.py` → Redis |
| DB overwhelmed by 10k req/s | Redis Bouncer filters 99% | `main.py` |
| Slow synchronous DB writes | Redis Stream queue + Worker | `main.py` → `worker.py` |
| Race condition in Oracle | Pessimistic `FOR UPDATE` lock | `worker.py` |
| Inventory stuck if worker crashes | Reservation watchdog timer | `main.py` watchdog |
| Stale stock shown in UI | WebSocket Pub/Sub push | `main.py` listener |
| Slow page load with many products | Server-side pagination | `main.py` + `app.js` |
| Cart items lost if user abandons | Global cart watchdog (40s) | `main.py` |
| Two carts created on rapid clicks | Client-side UUID generation | `app.js` |

---

*Built as a learning demonstration of production-grade flash sale architecture — Redis, FastAPI, Oracle, WebSockets, and beyond.*
