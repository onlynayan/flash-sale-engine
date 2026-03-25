from locust import HttpUser, task, between
import random
import uuid

class FlashSaleBot(HttpUser):
    # The base host for the test. Adding it here prevents the "No host" error.
    host = "http://127.0.0.1:8000"
    
    # Bots wait between 0.5s and 2s to feel like real humans
    wait_time = between(0.5, 2.0) 

    def on_start(self):
        # Using a valid user ID from your users2 table to satisfy FK constraints
        self.user_id = 1 
        self.cart_id = str(uuid.uuid4())
        self.items_in_cart = 0

    @task(5)
    def add_random_product_to_cart(self):
        # Picking from the first 24 products for high competition
        product_id = random.randint(1, 24)
        payload = {
            "user_id": self.user_id,
            "product_id": product_id,
            "cart_id": self.cart_id
        }
        with self.client.post("/cart/add", json=payload, catch_response=True, name="Add to Cart Button") as response:
            if response.status_code == 200:
                self.items_in_cart += 1
                response.success()
            elif response.status_code == 400:
                response.success() # Out of stock is expected behavior
            else:
                response.failure(f"Cart Add failed: {response.status_code}")

    @task(3) # Increased frequency slightly so you see it more in the report
    def complete_payment(self):
        # Don't try to checkout if the cart is empty
        if self.items_in_cart == 0:
            return
            
        payload = {
            "user_id": self.user_id,
            "cart_id": self.cart_id
        }
        
        # We label this "Complete Payment Button" so it's easy to find in Locust
        with self.client.post("/cart/checkout", json=payload, catch_response=True, name="Complete Payment Button") as response:
            if response.status_code == 200:
                # SUCCESS! reset the cart state for the next cycle
                self.cart_id = str(uuid.uuid4())
                self.items_in_cart = 0
                response.success()
            elif response.status_code == 400:
                # "Cart expired" - assume the watchdog killed it
                self.items_in_cart = 0
                self.cart_id = str(uuid.uuid4())
                response.success()
            else:
                response.failure(f"Checkout failed: {response.status_code}")

    @task(2)
    def browse_homepage(self):
        self.client.get("/display", name="Homepage View")

    @task(3) # High frequency health check to verify server stability
    def health_check(self):
        self.client.get("/", name="Server Health Check")