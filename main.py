import os
import json
import asyncio
import redis.asyncio as redis  # Use the async version
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from redis.exceptions import ResponseError
from database import engine, Base, get_db
import models, schemas
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

load_dotenv()

# 1. Initialize Redis Client for general commands (Fast)
redis_client = redis.from_url(
    os.getenv("REDIS_URL"),
    decode_responses=True,
    socket_connect_timeout=5,  # Don't hang forever if network blocks Redis
    socket_timeout=5,
)

# Initialize a separate Redis Client strictly for PubSub (No timeout)
redis_pubsub_client = redis.from_url(
    os.getenv("REDIS_URL"),
    decode_responses=True,
    socket_connect_timeout=5,
    # No socket_timeout here! The listener needs to block indefinitely
)

# Create the background task global variables
pubsub_task = None
worker_task = None  # NEW: holds the embedded worker loop

# ============================================================
# EMBEDDED WORKER — Runs inside FastAPI, no separate process!
# ============================================================
STREAM_KEY = "order_stream"
GROUP_NAME = "flash_sale_group"
CONSUMER_NAME = f"embedded_worker_{os.getpid()}"

# Dedicated SQLAlchemy engine for the worker coroutine
worker_engine = create_async_engine(os.getenv("DATABASE_URL"), echo=False)
WorkerSession = sessionmaker(worker_engine, class_=AsyncSession, expire_on_commit=False)

async def process_orders():
    """The embedded order consumer. Runs as a background asyncio task inside FastAPI."""
    print("🚀 Embedded Worker started. Waiting for messages on the queue...")

    worker_redis = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)

    # Ensure Consumer Group exists
    try:
        await worker_redis.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        print(f"✅ Worker: Consumer Group '{GROUP_NAME}' ready.")
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            print(f"Worker Group Error: {e}")

    try:
        while True:
            result = await worker_redis.xreadgroup(
                GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=1, block=2000
            )

            if not result:
                # block=2000 timed out — no new messages, loop again
                continue

            message_id = result[0][1][0][0]
            payload_str = result[0][1][0][1]["payload"]
            order_data = json.loads(payload_str)

            user_id = order_data["user_id"]
            product_id = order_data.get("product_id")
            order_id = order_data.get("order_id")
            items = order_data.get("items")

            print(f"📦 Worker received payload from User {user_id}. Processing...")

            # --- BULK CART CHECKOUT ---
            if items is not None:
                reservation_key = f"cart:{order_id}:items"
                has_reservation = await worker_redis.exists(reservation_key)

                if not has_reservation:
                    print(f"⌛ Cart {order_id} expired! Discarding.")
                    await worker_redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                    continue

                async with WorkerSession() as db:
                    try:
                        async with db.begin():
                            for pid in items:
                                query = select(models.Product).where(models.Product.id == int(pid)).with_for_update()
                                db_result = await db.execute(query)
                                product = db_result.scalars().first()
                                if product and product.stock > 0:
                                    product.stock -= 1
                                    db.add(models.Order(user_id=user_id, product_id=int(pid)))
                                else:
                                    print(f"⚠️ 0 stock for Product {pid}, skipping.")
                        await worker_redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                        await worker_redis.delete(reservation_key)
                        print(f"✅ Bulk Cart {order_id} processed and saved to DB.")
                    except Exception as e:
                        print(f"❌ DB Error (bulk): {e}")
                        await asyncio.sleep(1)
                continue

            # --- SINGLE ITEM ORDER (legacy) ---
            if order_id:
                reservation_key = f"reservation:{order_id}"
            else:
                reservation_key = f"reservation:{user_id}:{product_id}"

            has_reservation = await worker_redis.exists(reservation_key)
            if not has_reservation:
                print(f"⌛ Reservation for Product {product_id} expired! Discarding.")
                await worker_redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                continue

            async with WorkerSession() as db:
                try:
                    async with db.begin():
                        query = select(models.Product).where(models.Product.id == product_id).with_for_update()
                        db_result = await db.execute(query)
                        product = db_result.scalars().first()

                        if product and product.stock > 0:
                            product.stock -= 1
                            db.add(models.Order(user_id=user_id, product_id=product_id))
                            await worker_redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                            await worker_redis.delete(reservation_key)
                            print(f"✅ Order for Product {product_id} saved to DB.")
                        else:
                            print(f"⚠️ 0 stock in DB for Product {product_id}. Rolling back Redis.")
                            new_stock = await worker_redis.incr(f"stock:{product_id}")
                            await worker_redis.publish(
                                "stock_updates",
                                json.dumps({"product_id": product_id, "stock": new_stock})
                            )
                            await worker_redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                            await worker_redis.delete(reservation_key)
                except Exception as e:
                    print(f"❌ DB Error (single): {e}")
                    await asyncio.sleep(1)

    except asyncio.CancelledError:
        print("🛑 Embedded Worker shutting down...")
    finally:
        await worker_redis.aclose()

async def redis_pubsub_listener():
    # Upstash Serverless Redis occasionally drops connections. 
    # We MUST wrap the listener in an infinite loop so it reconnects if it crashes!
    while True:
        try:
            pubsub = redis_pubsub_client.pubsub()
            await pubsub.subscribe("stock_updates")
            print("🎧 PubSub listener connected and waiting for broadcasts...")
            
            # Manual Keep-Alive loop because Upstash Serverless drops idle connections
            # and redis-py's health_check_interval causes asyncio deadlocks!
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=10.0)
                if message is not None:
                    if message["type"] == "message":
                        data = message["data"]
                        # Broadcast safely to all connected displays
                        for connection in list(active_connections):
                            try:
                                await connection.send_text(data)
                            except Exception as e:
                                print(f"WS Send Error: {e}")
                else:
                    # After 10 seconds of no messages, we manually ping Upstash to keep the socket fully alive!
                    await pubsub.ping()
        except Exception as e:
            print(f"⚠️ PubSub Listener Error (Network Drop): {e}. Reconnecting in 2 seconds...")
            await asyncio.sleep(2)

import uuid

async def reservation_rollback_timer(order_id: str, user_id: int, product_id: int):
    """
    An ultra-efficient asyncio watchdog.
    """
    reservation_key = f"reservation:{order_id}"
    
    print(f"\n⏱️  [START] 30s Watchdog started for Order {order_id} (Product {product_id})")
    
    # Print the timer countdown every 5 seconds so we can track it!
    for remaining in range(30, 0, -5):
        print(f"⏳ Order {order_id} -> {remaining} seconds remaining until auto-restock...")
        await asyncio.sleep(5)
    
    # 1. Did the background worker already process and delete this reservation?
    exists = await redis_client.exists(reservation_key)
    
    if exists:
        # 2. Worker failed or crashed! Time to release the stock back to the public!
        print(f"⏰ [EXPIRED] Order {order_id} EXPIRED safely! Auto-refilling virtual stock for Product {product_id}...")
        
        # Destroy the ticket
        await redis_client.delete(reservation_key)
        
        # Refill the stock in the Bouncer
        new_stock = await redis_client.incr(f"stock:{product_id}")
        
        # Broadcast the update to the UI
        import json
        await redis_client.publish(
            "stock_updates",
            json.dumps({"product_id": product_id, "stock": int(new_stock)})
        )

async def bulk_reservation_rollback_timer(cart_id: str):
    """
    Watchdog for the Global Cart (40 seconds strict).
    Does NOT reset when new items are added!
    """
    cart_items_key = f"cart:{cart_id}:items"
    
    print(f"\n⏱️  [START] 40s CART Watchdog started for Cart {cart_id}")
    
    for remaining in range(40, 0, -10):
        print(f"⏳ Cart {cart_id} -> {remaining} seconds remaining until auto-restock...")
        await asyncio.sleep(10)
        
    exists = await redis_client.exists(cart_items_key)
    if exists:
        # Watchdog woke up and the cart hasn't been processed by the worker!
        print(f"⏰ [EXPIRED] Cart {cart_id} EXPIRED! Auto-refilling all items...")
        
        items = await redis_client.lrange(cart_items_key, 0, -1)
        await redis_client.delete(cart_items_key)
        
        import json
        for product_id in items:
            new_stock = await redis_client.incr(f"stock:{product_id}")
            await redis_client.publish(
                "stock_updates",
                json.dumps({"product_id": int(product_id), "stock": int(new_stock)})
            )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print("Connecting to Oracle Database...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables verified/created.")

    # 2. Test Redis Connection during startup
    try:
        await redis_client.ping()
        print("✅ Successfully connected to Upstash Redis!")
    except Exception as e:
        print(f"❌ Redis Initial Connection Failed (will try to auto-reconnect): {e}")
        
    # 3. Start background tasks: PubSub listener + Embedded Worker
    global pubsub_task, worker_task
    pubsub_task = asyncio.create_task(redis_pubsub_listener())
    worker_task = asyncio.create_task(process_orders())
    
    yield
    
    # --- SHUTDOWN ---
    if pubsub_task:
        pubsub_task.cancel()
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    await redis_client.close()
    await redis_pubsub_client.close()
    print("Server shutting down...")


app = FastAPI(title="Pro Flash Sale Engine", lifespan=lifespan)

# Serve the static files (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize the templates directory
templates = Jinja2Templates(directory="templates")

# Store active WebSocket connections
active_connections: list[WebSocket] = []

@app.get("/")
async def health_check():
    return {"status": "online", "redis": "connected"}

# ---------------------------------------------------------
# NEW: SERVE THE FRONTEND UI
# ---------------------------------------------------------
@app.get("/display")
async def station_display(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ---------------------------------------------------------
# UPDATED: GLOBAL WEBSOCKET FOR ALL PRODUCTS
# ---------------------------------------------------------
@app.websocket("/ws/catalog")
async def websocket_catalog_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        # We no longer send individual stock on connect. 
        # The frontend will fetch the initial state from GET /products/
        while True:
            # Keep the async loop alive waiting
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        print("A client disconnected from the Catalog Display.")

# ---------------------------------------------------------
# NEW: ADMIN ENDPOINT TO SYNC STOCK FROM DB TO REDIS
# ---------------------------------------------------------
@app.post("/admin/sync-redis/{product_id}")
async def sync_redis(product_id: int, db: AsyncSession = Depends(get_db)):
    """Use this to 'prime' the Redis bouncer with current DB stock"""
    result = await db.execute(select(models.Product).where(models.Product.id == product_id))
    product = result.scalars().first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found in DB")
    
    # Set the stock in Redis
    await redis_client.set(f"stock:{product_id}", product.stock)
    return {"message": f"Product {product_id} stock synced to Redis: {product.stock}"}

# ---------------------------------------------------------
# UPDATED: ASYNC MESSAGE QUEUE ORDER PLACEMENT (Ingestion API via Redis Streams)
# ---------------------------------------------------------
@app.post("/orders/", response_model=schemas.OrderQueuedResponse)
async def create_order(order: schemas.OrderCreate):
    # Notice we no longer ask for `db: AsyncSession`! This route NEVER hits Oracle now.
    
    # --- PHASE 1: THE BOUNCER (REDIS) ---
    new_redis_stock = await redis_client.decr(f"stock:{order.product_id}")
    
    if new_redis_stock < 0:
        await redis_client.incr(f"stock:{order.product_id}") # Undo the counter since we rejected
        raise HTTPException(status_code=400, detail="Out of stock (Filtered by Redis)")
        
    # --- PHASE 1.5: THE RESERVATION TIMER ---
    # Create a UNIQUE virtual reservation token using UUID.
    order_id = str(uuid.uuid4())
    reservation_key = f"reservation:{order_id}"
    await redis_client.set(reservation_key, "reserved")
    
    # Launch the background watchdog to auto-refill if worker fails/ignores this within 30s!
    asyncio.create_task(reservation_rollback_timer(order_id, order.user_id, order.product_id))

    # --- BROADCAST TO ALL STATION DISPLAYS VIA PUBSUB ---
    import json
    await redis_client.publish(
        "stock_updates", 
        json.dumps({"product_id": order.product_id, "stock": int(new_redis_stock)})
    )

    # --- PHASE 2: EVENT PUBLISHING (Message Queue via Redis STREAMS) ---
    try:
        # We must pass the unique order_id to the worker so it knows WHICH reservation to delete!
        payload = json.dumps({"order_id": order_id, "user_id": order.user_id, "product_id": order.product_id})
        
        # We put the "Payload" onto the Redis Stream for true reliability
        await redis_client.xadd("order_stream", {"payload": payload})
        
        # We instantly turn back around to the user in < 2ms!
        return {"message": "Order placed! A background worker is processing it.", "status": "queued"}

    except Exception as e:
        # If the queue itself crashes, we revert the Bouncer
        current_stock = await redis_client.incr(f"stock:{order.product_id}")
        await redis_client.publish(
            "stock_updates", 
            json.dumps({"product_id": order.product_id, "stock": int(current_stock)})
        )
                
        raise HTTPException(status_code=500, detail=f"Queue Error: {str(e)}")

# ---------------------------------------------------------
# NEW: BULK CART ENDPOINTS (Phase 9)
# ---------------------------------------------------------
@app.post("/cart/add")
async def add_to_cart(cart_item: schemas.CartAdd):
    # --- PHASE 1: THE BOUNCER (REDIS) ---
    new_redis_stock = await redis_client.decr(f"stock:{cart_item.product_id}")
    
    if new_redis_stock < 0:
        await redis_client.incr(f"stock:{cart_item.product_id}")
        raise HTTPException(status_code=400, detail="Out of stock (Filtered by Redis)")
        
    import json
    await redis_client.publish(
        "stock_updates", 
        json.dumps({"product_id": cart_item.product_id, "stock": int(new_redis_stock)})
    )
    
    cart_id = cart_item.cart_id
    
    if not cart_id:
        cart_id = str(uuid.uuid4())
        
    cart_items_key = f"cart:{cart_id}:items"
    
    # Check if this cart is empty BEFORE we push the new item!
    existing_items = await redis_client.llen(cart_items_key)
    is_first_item = (existing_items == 0)
    
    # Save the product to the user's list in Redis
    await redis_client.rpush(cart_items_key, cart_item.product_id)
    
    # Only start the strict 40-second timer on the VERY FIRST item!
    if is_first_item:
        asyncio.create_task(bulk_reservation_rollback_timer(cart_id))
        
    return {"message": "Added to cart!", "cart_id": cart_id}


@app.post("/cart/checkout")
async def checkout_cart(checkout: schemas.CartCheckout):
    cart_items_key = f"cart:{checkout.cart_id}:items"
    
    # 1. Did the 40-second Watchdog already delete their cart?
    exists = await redis_client.exists(cart_items_key)
    if not exists:
        raise HTTPException(status_code=400, detail="Cart expired! Your items were released.")
        
    # 2. Grab all their reserved items from Redis
    items = await redis_client.lrange(cart_items_key, 0, -1)
    
    # 3. Put the BULK payload onto the Redis Stream for the worker
    import json
    payload = json.dumps({
        "order_id": checkout.cart_id, # Re-use the cart_id as the order_id for the worker
        "user_id": checkout.user_id, 
        "items": items
    })
    
    try:
        await redis_client.xadd("order_stream", {"payload": payload})
        return {"message": "Checkout successful! A background worker is processing your bulk order."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Queue Error: {str(e)}")

@app.post("/cart/remove")
async def remove_from_cart(remove_item: schemas.CartRemove):
    cart_items_key = f"cart:{remove_item.cart_id}:items"
    
    # 1. Did the 40-second Watchdog already delete their cart?
    exists = await redis_client.exists(cart_items_key)
    if not exists:
        raise HTTPException(status_code=400, detail="Cart expired! Items already released.")
        
    # 2. Delete exactly ONE instance of this product ID from the Redis List
    # LREM format: name, count, value. 
    # count=1 means remove the first match starting from head.
    removed_count = await redis_client.lrem(cart_items_key, 1, str(remove_item.product_id))
    
    if removed_count == 0:
         raise HTTPException(status_code=404, detail="Item not found in your active cart.")
         
    # 3. IF SUCCESSFUL REMOVAL: Restore the stock to the Bouncer!
    new_stock = await redis_client.incr(f"stock:{remove_item.product_id}")
    
    # 4. Broadcast the returned stock globally so others can buy it!
    import json
    await redis_client.publish(
        "stock_updates", 
        json.dumps({"product_id": remove_item.product_id, "stock": int(new_stock)})
    )
    
    # 5. Check if the Cart is now empty. If so, clean up the reservation to save memory.
    remaining_items = await redis_client.llen(cart_items_key)
    if remaining_items == 0:
        await redis_client.delete(cart_items_key)
        
    return {"message": "Item removed and stock refunded."}

# ---------------------------------------------------------
# CREATE A USER
# ---------------------------------------------------------
@app.post("/users/", response_model=schemas.UserResponse)
async def create_user(user: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    db_user = models.User(username=user.username)
    db.add(db_user)
    try:
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except Exception as e:
        await db.rollback()
        print(f"CREATE USER ERROR: {e}")
        raise HTTPException(status_code=400, detail=f"Error: {e}")

# ---------------------------------------------------------
# CREATE A PRODUCT
# ---------------------------------------------------------
@app.post("/products/", response_model=schemas.ProductResponse)
async def create_product(product: schemas.ProductCreate, db: AsyncSession = Depends(get_db)):
    db_product = models.Product(**product.model_dump())
    db.add(db_product)
    await db.commit()
    await db.refresh(db_product)
    
    # NEW: Automatically introduce the new product to the Redis Bouncer!
    await redis_client.set(f"stock:{db_product.id}", db_product.stock)
    
    return db_product

from sqlalchemy import func
import math

# ---------------------------------------------------------
# GET ALL PRODUCTS (Enhanced with Pagination & Live Redis Stock)
# ---------------------------------------------------------
@app.get("/products/", response_model=schemas.PaginatedProductResponse)
async def get_products(
    skip: int = 0, 
    limit: int = 12, 
    db: AsyncSession = Depends(get_db)
):
    # 1. Get the TOTAL count of products in the database
    count_query = await db.execute(select(func.count()).select_from(models.Product))
    total_count = count_query.scalar()
    
    # 2. Get the specific PAGE of products (using offset and limit)
    result = await db.execute(
        select(models.Product).offset(skip).limit(limit)
    )
    products = result.scalars().all()
    
    # NEW: Override the DB stock with the LIVE Redis stock!
    for product in products:
        live_stock = await redis_client.get(f"stock:{product.id}")
        if live_stock is not None:
            # Important: Don't write this back to the DB during a GET, 
            # just change it in the response model we return to the user.
            product.stock = int(live_stock)
            
    # Calculate pages
    total_pages = math.ceil(total_count / limit) if total_count > 0 else 1
    current_page = (skip // limit) + 1
            
    return schemas.PaginatedProductResponse(
        items=products,
        total=total_count,
        page=current_page,
        pages=total_pages
    )