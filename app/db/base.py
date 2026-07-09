"""Declarative base. Import models here so Base.metadata sees every table."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Imported for metadata side effects (alembic autogenerate, tests' create_all).
from app.models.event import Event  # noqa: E402,F401
from app.models.raw_post import RawPost  # noqa: E402,F401
