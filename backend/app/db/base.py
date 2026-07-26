"""SQLAlchemy declarative base and shared metadata.

Kept separate from session.py so the ORM models and Alembic's env.py can import
`Base.metadata` without pulling in engine/connection setup (which needs a URL).
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every ORM model. `Base.metadata` is the schema that
    Alembic autogenerates migrations against."""
