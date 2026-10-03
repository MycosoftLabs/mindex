"""MINDEX ledger persistence; SQLite schema translation is for offline tests only."""
from sqlalchemy import (
    JSON, Column, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    UniqueConstraint,
)

metadata = MetaData(schema="ledger")
records = Table(
    "provenance_record", metadata,
    Column("id", String(36), primary_key=True),
    Column("issuer", String(512), nullable=False),
    Column("subject", String(256), nullable=False),
    Column("tenant_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("source", JSON, nullable=False),
    Column("state", String(24), nullable=False),
    Column("qualification", String(24), nullable=False),
    Column("version", Integer, nullable=False),
    Column("approval", JSON),
    Column("chain", String(32)),
    Column("transaction_id", String(256)),
    Column("verification", JSON),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
    UniqueConstraint("issuer", "subject", "tenant_id", "project_id", "idempotency_key",
                     name="uq_provenance_registration"),
)
events = Table(
    "provenance_event", metadata,
    Column("id", String(36), primary_key=True),
    Column("record_id", String(36), ForeignKey("ledger.provenance_record.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("from_state", String(24), nullable=False),
    Column("to_state", String(24), nullable=False),
    Column("actor", JSON, nullable=False),
    Column("detail", JSON, nullable=False),
    Column("created_at", String(32), nullable=False),
    UniqueConstraint("record_id", "idempotency_key", name="uq_provenance_event_replay"),
    UniqueConstraint("record_id", "version", name="uq_provenance_event_version"),
)
queue = Table(
    "provenance_queue", metadata,
    Column("record_id", String(36), ForeignKey("ledger.provenance_record.id"), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("last_error", Text),
    Column("updated_at", String(32), nullable=False),
)
receipts = Table(
    "provenance_receipt", metadata,
    Column("id", String(36), primary_key=True),
    Column("record_id", String(36), ForeignKey("ledger.provenance_record.id"), nullable=False),
    Column("chain", String(32), nullable=False),
    Column("adapter", String(32), nullable=False),
    Column("receipt_id", String(256), nullable=False),
    Column("transaction_id", String(256), nullable=False),
    Column("accepted", Integer, nullable=False),
    Column("created_at", String(32), nullable=False),
    UniqueConstraint("chain", "adapter", "receipt_id", name="uq_provenance_receipt"),
    UniqueConstraint("chain", "adapter", "transaction_id", name="uq_provenance_transaction"),
)
Index("ix_provenance_owner", records.c.issuer, records.c.subject,
      records.c.tenant_id, records.c.project_id, records.c.created_at)
Index("ix_provenance_queue_status", queue.c.status, queue.c.updated_at)
