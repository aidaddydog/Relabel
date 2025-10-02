
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Boolean, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .core.database import Base

class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ClientAuth(Base):
    __tablename__ = "client_auth"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    code_hash: Mapped[str] = mapped_column(String(512), index=True)
    code_plain: Mapped[str | None] = mapped_column(String(6), nullable=True)  # one-time display
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MetaKV(Base):
    __tablename__ = "meta_kv"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    k: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    v: Mapped[str] = mapped_column(Text, default="")
    remark: Mapped[str] = mapped_column(String(255), default="")

class TrackingFile(Base):
    __tablename__ = "tracking_file"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracking_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_path: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    print_status: Mapped[str] = mapped_column(String(16), default="not_printed")  # not_printed|printed|reprinted
    print_count: Mapped[int] = mapped_column(Integer, default=0)
    first_print_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_print_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_print_client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

class PrintEvent(Base):
    __tablename__ = "print_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    access_code: Mapped[str] = mapped_column(String(6), index=True)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    tracking_no: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32))  # success|fail|success_reprint
    host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    printer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mac_list: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    ip_list: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON string
    pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

class OrderMapping(Base):
    __tablename__ = "order_mapping"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), index=True)
    tracking_no: Mapped[str] = mapped_column(String(64), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("order_id", name="uq_order_id"),)
