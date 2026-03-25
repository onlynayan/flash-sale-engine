import urllib.request
import json

products = [
    {"name": "iPad",           "price": 200000, "stock": 4},
    {"name": "Keyboard",       "price": 4000,   "stock": 4},
    {"name": "Ram",            "price": 12000,  "stock": 2},
    {"name": "SSD",            "price": 16000,  "stock": 5},
    {"name": "Iphone",         "price": 120000, "stock": 5},
    {"name": "Samsung TV",     "price": 50000,  "stock": 10},
    {"name": "Laptop",         "price": 90000,  "stock": 10},
    {"name": "Headphone",      "price": 8000,   "stock": 10},
    {"name": "Washing Machine","price": 80000,  "stock": 8},
    {"name": "PS5",            "price": 70000,  "stock": 10},
    {"name": "Mystery Box",    "price": 500,    "stock": 5},
    {"name": "Earbud",         "price": 5000,   "stock": 7},
    {"name": "Smart Watch",    "price": 7000,   "stock": 2},
    {"name": "Mouse",          "price": 3000,   "stock": 8},
    {"name": "LED Monitor",    "price": 60000,  "stock": 3},
]

for p in products:
    data = json.dumps(p).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/products/",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        print(f"Created: {result['name']} (id={result['id']})")
