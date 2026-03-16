import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# 1. Load the secret URL from the .env file
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# 2. Create the Async Engine (This acts as our Connection Pool)
engine = create_async_engine(DATABASE_URL, echo=True)

# 3. Create a Session Factory (This hands out connections to API requests)
SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 4. Base class for our database models (Tables)
Base = declarative_base()

# 5. Dependency injection: Give each API request its own database session, then close it safely
async def get_db():
    async with SessionLocal() as session:
        yield session