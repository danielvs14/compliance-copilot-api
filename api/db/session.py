from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from ..config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


def get_engine():
    return engine
