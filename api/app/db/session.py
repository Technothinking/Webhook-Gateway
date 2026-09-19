from app.db.database import engine
from sqlalchemy.orm import sessionmaker

SessionLocal = sessionmaker(
    bind= engine,
    autoflush= False,
    autocommit= False
)