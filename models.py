from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Identity
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users2"
    
    id = Column(Integer, Identity(start=1), primary_key=True)
    username = Column(String(100), unique=True, index=True)

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, Identity(start=1), primary_key=True)
    name = Column(String(100), index=True)
    price = Column(Integer) # Store money in cents to avoid decimal bugs!
    stock = Column(Integer) # We will optimize this later with Redis

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, Identity(start=1), primary_key=True)
    user_id = Column(Integer, ForeignKey("users2.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    created_at = Column(DateTime, default=func.now())