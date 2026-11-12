"""Declarative base shared by all ORM models."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names, e.g. "ck_machines_type", "fk_sensors_machine_id_machines".
# Without this Postgres invents names (machines_type_check1), and later migrations that need to
# drop/alter a constraint can't reliably refer to it. Set once, before the first migration.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    # column_0_N_name = ALL columns ("uq_sensors_machine_id_name"), not just the first one, so a
    # composite unique constraint isn't misread as "machine_id alone is unique".
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
