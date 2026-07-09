"""Declarative base.

Consumers that need the full metadata (alembic env, test create_all) must import
the model modules themselves — importing them here would be circular.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
