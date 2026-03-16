import asyncio
import os
import json
import redis.asyncio as redis
from redis.exceptions import ResponseError
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import models  # Assuming models.py exists

# Load environment variables
load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# Configure Redis Streams
STREAM_KEY = "order_stream"
GROUP_NAME = "flash_sale_group"
CONSUMER_NAME = f"worker_{os.getpid()}"  # Unique name per worker instance

# Engine & Session for the Worker
# Important: The worker needs its own connection pool!
engine = create_async_engine(DATABASE_URL, echo=False)  # Set echo=False to avoid terminal spam
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def process_orders():
    print("🚀 Background Worker (Consumer) started. Waiting for messages on the queue...")
    
    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    # Ensure Consumer Group Exists for Redis Streams
    try:
        await redis_client.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        print(f"✅ Created Consumer Group '{GROUP_NAME}' on stream '{STREAM_KEY}'")
    except ResponseError as e:
        if "BUSYGROUP" in str(e):
            # The group already exists, this is totally fine and expected!
            pass
        else:
            # Assuming 'ent' was a typo and the intent was to raise an exception or print
            # Sticking to print for consistency with original code's error handling for non-BUSYGROUP errors
            print(f"Error creating consumer group: {e}")

    try:
        while True:
            # XREADGROUP pulls from the stream and assigns it to this specific worker
            # ">" means read new messages not yet delivered to other consumers
            # block=0 means wait forever
            result = await redis_client.xreadgroup(
                GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=1, block=0
            )
            
            if result:
                # Extract payload from Redis Stream result structure
                message_id = result[0][1][0][0]
                payload_str = result[0][1][0][1]["payload"]
                
                order_data = json.loads(payload_str)
                user_id = order_data["user_id"]
                product_id = order_data.get("product_id") # Use .get() so it doesn't crash on bulk carts!

                # Support the new UUID order_id, fallback to old method for legacy messages
                order_id = order_data.get("order_id")
                # NEW: Support bulk items from Cart checkout
                items = order_data.get("items")
                
                print(f"📦 Worker received payload from User {user_id}. Processing...")
                
                # --- PHASE 3: THE BULK RESERVATION VALIDATION (PHASE 9) ---
                if items is not None:
                    reservation_key = f"cart:{order_id}:items"
                    has_reservation = await redis_client.exists(reservation_key)
                    
                    if not has_reservation:
                        print(f"⌛ Cart Reservation {order_id} Expired! Watchdog already refilled it. Trashing ticket.")
                        await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                        continue
                        
                    async with AsyncSessionLocal() as db:
                        try:
                            async with db.begin():
                                for pid in items:
                                    query = select(models.Product).where(models.Product.id == int(pid)).with_for_update()
                                    db_result = await db.execute(query)
                                    product = db_result.scalars().first()
                                    
                                    if product and product.stock > 0:
                                        product.stock -= 1
                                        new_order = models.Order(user_id=user_id, product_id=int(pid))
                                        db.add(new_order)
                                    else:
                                        print(f"⚠️ Warning: Worker found 0 stock for Product {pid} in bulk cart. Skipping.")
                                        
                                # DESTROY the reservation so the Waiter doesn't auto-refill it later!
                                await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                                await redis_client.delete(reservation_key)
                                print(f"✅ Bulk Cart Order {order_id} safely processed (ACKed) and persisted to Oracle.")
                        except Exception as e:
                            print(f"❌ Oracle/System Error while processing bulk order: {e}")
                            await asyncio.sleep(1)
                            
                    continue # Finished with bulk, avoid legacy code below!

                # --- PHASE 3: LEGACY SINGLE ITEM LOGIC ---
                product_id = order_data.get("product_id")
                
                # Before talking to Oracle, check if the 30-second reservation still exists
                if order_id:
                    reservation_key = f"reservation:{order_id}"
                else:
                    reservation_key = f"reservation:{user_id}:{product_id}"
                    
                has_reservation = await redis_client.exists(reservation_key)
                
                if not has_reservation:
                    print(f"⌛ Reservation Expired! The Waiter already refilled Product {product_id}. Trashing ticket.")
                    await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                    continue # Skip the Oracle write entirely
                
                # The Secure Write (Database Phase)
                async with AsyncSessionLocal() as db:
                    try:
                        async with db.begin():
                            # The Worker safely locks the row and updates Oracle
                            query = select(models.Product).where(models.Product.id == product_id).with_for_update()
                            db_result = await db.execute(query)
                            product = db_result.scalars().first()

                            if product and product.stock > 0:
                                product.stock -= 1
                                new_order = models.Order(user_id=user_id, product_id=product_id)
                                db.add(new_order)
                                # ACKNOWLEDGE message so it's removed from pending state
                                await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                                # DESTROY the reservation so the Waiter doesn't auto-refill it later!
                                await redis_client.delete(reservation_key)
                                print(f"✅ Order for Product {product_id} safely processed (ACKed) and persisted to Oracle.")
                            else:
                                print(f"⚠️ Warning: Worker found 0 stock in Oracle for Product {product_id}. Order rejected.")
                                
                                # --- TRUE RELIABILITY: ROLLBACK --- 
                                print(f"🔄 Rolling back Redis stock for Product {product_id} to ensure UI consistency")
                                new_stock = await redis_client.incr(f"stock:{product_id}")
                                
                                # Inform the UI via Pub/Sub so users see the restored stock
                                await redis_client.publish(
                                    "stock_updates", 
                                    json.dumps({"product_id": product_id, "stock": new_stock})
                                )
                                
                                # Acknowledge the failed message so we discard it
                                await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                                # DESTROY the reservation so Waiter doesn't double-refill!
                                await redis_client.delete(reservation_key)
                    except Exception as e:
                        print(f"❌ Oracle/System Error while processing order: {e}")
                        # WE DO NOT XACK HERE. 
                        # The ticket stays "Pending" in Redis Streams. When DB recovers, it can be retried.
                        await asyncio.sleep(1) # Prevent endless loop crash

    except asyncio.CancelledError:
        print("Worker shutting down...")
    finally:
        await redis_client.close()

if __name__ == "__main__":
    asyncio.run(process_orders())
