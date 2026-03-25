from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.sql import func
from database import Base

# NOTE ON AUTO-INCREMENT:
# We intentionally do NOT use Oracle's Identity() here.
# SQLAlchemy's default autoincrement=True is database-agnostic and works
# on both Oracle AND PostgreSQL without any code changes.
# Just swap the DATABASE_URL in your .env file to switch databases!

class User(Base):
    __tablename__ = "users2"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True)

class Product(Base):
    __tablename__ = "products"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), index=True)
    price = Column(Integer)  # Store in cents to avoid decimal bugs!
    stock = Column(Integer)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users2.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    created_at = Column(DateTime, default=func.now())