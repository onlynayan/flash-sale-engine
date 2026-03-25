"""
Bulk Product Seeder for Load Testing
=====================================
Inserts a large number of random products DIRECTLY into the database
(bypasses the API for maximum speed) and syncs stock to Redis.

Usage:
    python seed_bulk.py            # Inserts 1,000 products (quick test)
    python seed_bulk.py 10000      # Inserts 10,000 products
    python seed_bulk.py 40000      # Inserts 40,000 products (full load test)

The script also syncs all inserted products to Redis automatically.
"""

import asyncio
import random
import sys
import os
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import redis.asyncio as aioredis

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
TOTAL_PRODUCTS = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
BATCH_SIZE = 500   # Commit every N products (safe for Oracle & PostgreSQL)

# Realistic product catalog — same naming style as your existing products
PRODUCT_CATALOG = [
    # Phones & Tablets
    ("iPhone 16 Pro", 170000), ("iPhone 16", 130000), ("iPhone 15", 110000),
    ("Samsung Galaxy S25", 150000), ("Samsung Galaxy A55", 60000),
    ("Google Pixel 9", 100000), ("OnePlus 13", 90000),
    ("iPad Pro 13\"", 220000), ("iPad Mini", 80000), ("iPad Air", 120000),
    # Laptops
    ("MacBook Air M3", 250000), ("MacBook Pro M4", 350000),
    ("Dell XPS 15", 280000), ("HP Spectre x360", 220000),
    ("ASUS ROG Zephyrus", 300000), ("Acer Nitro 5", 130000),
    ("Razer Blade 15", 320000), ("Surface Laptop 5", 200000),
    # Accessories
    ("Mechanical Keyboard", 8000), ("Wireless Keyboard", 4000),
    ("Gaming Mouse", 5000), ("Wireless Mouse", 3000),
    ("USB-C Hub 7-Port", 3500), ("Thunderbolt Dock", 15000),
    ("Laptop Stand", 2500), ("Webcam 4K", 8000), ("Ring Light 26\"", 6000),
    # Audio
    ("Sony WH-1000XM5 Headphones", 35000), ("AirPods Pro 2", 30000),
    ("Samsung Galaxy Buds 3", 18000), ("JBL Flip 7 Speaker", 12000),
    ("Bose QC Ultra Earbuds", 28000),
    # Displays & TVs
    ("4K Monitor 27\"", 45000), ("Ultrawide Monitor 34\"", 80000),
    ("Gaming Monitor 144Hz", 35000), ("Samsung 55\" OLED TV", 180000),
    ("LED Monitor 24\"", 25000),
    # Storage & Memory
    ("SSD 1TB NVMe", 12000), ("SSD 2TB NVMe", 22000),
    ("RAM 16GB DDR5", 8000), ("RAM 32GB DDR5", 15000),
    ("MicroSD 256GB", 4000), ("HDD 4TB External", 15000),
    # Gaming
    ("PS5 Controller DualSense", 8500), ("Xbox Series Controller", 7000),
    ("Nintendo Switch OLED", 50000), ("Steam Deck 512GB", 80000),
    # Wearables
    ("Apple Watch Series 10", 55000), ("Samsung Galaxy Watch 7", 35000),
    ("Fitbit Charge 6", 18000), ("Garmin Forerunner 265", 42000),
    # Misc
    ("Power Bank 20000mAh", 4500), ("Wireless Charger 15W", 2000),
    ("GPU RTX 4060", 95000), ("CPU i9-14900K", 120000),
    ("Mystery Box", 500), ("Washing Machine 8kg", 80000),
    ("Smart Watch", 7000), ("Earbud", 5000), ("Mouse", 3000),
]


def generate_product():
    """Pick a random product from the catalog with randomized stock."""
    name, base_price = random.choice(PRODUCT_CATALOG)
    price = int(base_price * random.uniform(0.9, 1.1))  # ±10% price variation
    # 50% low stock (1-5), 35% medium (6-20), 15% high (21-50)
    stock = random.choices(
        [random.randint(1, 5), random.randint(6, 20), random.randint(21, 50)],
        weights=[50, 35, 15]
    )[0]
    return {"name": name, "price": price, "stock": stock}


async def seed():
    print(f"Seeding {TOTAL_PRODUCTS:,} products in batches of {BATCH_SIZE}...")

    # ─── DB Setup ───────────────────────────────
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set in .env")
        return

    db_type = "PostgreSQL" if "postgresql" in DATABASE_URL else "Oracle"
    print(f"Target database: {db_type}")

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ─── Redis Setup ────────────────────────────
    REDIS_URL = os.getenv("REDIS_URL")
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

    # ─── Batch Insert ───────────────────────────
    inserted = 0
    async with Session() as db:
        batch = []
        for i in range(TOTAL_PRODUCTS):
            batch.append(generate_product())

            if len(batch) >= BATCH_SIZE or i == TOTAL_PRODUCTS - 1:
                async with db.begin():
                    if "postgresql" in DATABASE_URL:
                        # PostgreSQL: use bulk VALUES for maximum speed
                        await db.execute(
                            text("INSERT INTO products (name, price, stock) VALUES (:name, :price, :stock)"),
                            batch
                        )
                    else:
                        # Oracle: same approach works
                        await db.execute(
                            text("INSERT INTO products (name, price, stock) VALUES (:name, :price, :stock)"),
                            batch
                        )
                inserted += len(batch)
                print(f"  Inserted {inserted:,} / {TOTAL_PRODUCTS:,} products...")
                batch = []

    print(f"\nDatabase insert complete! {inserted:,} products added.")

    # ─── Sync Stock to Redis ────────────────────
    if redis_client:
        print("Syncing stock to Redis...")
        async with Session() as db:
            result = await db.execute(text("SELECT id, stock FROM products"))
            rows = result.fetchall()
            # Use a Redis pipeline for fast bulk SET
            pipe = redis_client.pipeline()
            for row in rows:
                pipe.set(f"stock:{row[0]}", row[1])
            await pipe.execute()
            print(f"Synced {len(rows):,} products to Redis.")
        await redis_client.aclose()
    else:
        print("No REDIS_URL found — skipping Redis sync. Run sync_all.py manually.")

    await engine.dispose()
    print("\nDone! Restart uvicorn to see the new products in the UI.")


if __name__ == "__main__":
    asyncio.run(seed())
