import asyncio
import httpx

async def sync_all():
    """
    This script hits the admin endpoint in main.py for every product.
    It forces the fast Redis Bouncer to perfectly match the true Oracle Database stock.
    Run this script whenever you restart testing or want to reset the cache!
    """
    
    print("Starting System-Wide Inventory Sync (Oracle -> Redis Bouncer)...")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Fetch all products dynamically from the API!
            print("Fetching fully dynamic product list from Database...")
            catalog_response = await client.get('http://127.0.0.1:8000/products/')
            if catalog_response.status_code != 200:
                print(f"Failed to fetch products! HTTP {catalog_response.status_code}")
                return
                
            products = catalog_response.json()
            product_ids = [str(p["id"]) for p in products]
            print(f"Found {len(product_ids)} products to sync: {', '.join(product_ids)}")
            
            # 2. Sync every single product ID we found
            for pid in product_ids:
                try:
                    # Call the FastAPI Admin Endpoint
                    response = await client.post(f'http://127.0.0.1:8000/admin/sync-redis/{pid}')
                    
                    if response.status_code == 200:
                        data = response.json()
                        print(f"Success: {data.get('message')}")
                    else:
                        print(f"Failed to sync Product {pid}: HTTP {response.status_code}")
                        
                except Exception as e:
                    print(f"Could not connect for item {pid} - Is FastAPI running?")
    except Exception as e:
         print(f"Network error: {e}")

if __name__ == "__main__":
    asyncio.run(sync_all())
    print("\nSync Complete! Redis is now perfectly matching Oracle. The Flash Sale is ready!")
