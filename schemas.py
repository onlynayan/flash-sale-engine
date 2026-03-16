from pydantic import BaseModel
from typing import Optional
from datetime import datetime

# --- USER SCHEMAS ---
class UserCreate(BaseModel):
    username: str

class UserResponse(BaseModel):
    id: int
    username: str

    class Config:
        from_attributes = True

# --- PRODUCT SCHEMAS ---
class ProductCreate(BaseModel):
    name: str
    price: int # Remember, stored in cents!
    stock: int

class ProductResponse(BaseModel):
    id: int
    name: str
    price: int
    stock: int

    class Config:
        from_attributes = True

class PaginatedProductResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    pages: int

# --- ORDER SCHEMAS ---
class OrderCreate(BaseModel):
    user_id: int
    product_id: int

class OrderResponse(BaseModel):
    id: int
    user_id: int
    product_id: int
    created_at: datetime

    class Config:
        from_attributes = True

class OrderQueuedResponse(BaseModel):
    message: str
    status: str

# --- CART SCHEMAS ---
class CartAdd(BaseModel):
    user_id: int
    product_id: int
    cart_id: Optional[str] = None  # None if first item, UUID if subsequent

class CartCheckout(BaseModel):
    user_id: int
    cart_id: str

class CartRemove(BaseModel):
    user_id: int
    product_id: int
    cart_id: str