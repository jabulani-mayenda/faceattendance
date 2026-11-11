from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, Index, LargeBinary, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def now_utc(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), default='org_admin')
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class UserSession(Base):
    __tablename__ = 'user_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user = relationship('User')

class Person(Base):
    __tablename__ = 'people'
    id: Mapped[int] = mapped_column(primary_key=True)
    person_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default='education')
    status: Mapped[str] = mapped_column(String(20), default='active')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    @property
    def full_name(self): return f'{self.first_name} {self.last_name}'

class BiometricTemplate(Base):
    __tablename__ = 'biometric_templates'
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey('people.id', ondelete='CASCADE'), unique=True, index=True)
    encrypted_embedding: Mapped[bytes] = mapped_column(LargeBinary)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    enrolled_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    model_version: Mapped[str] = mapped_column(String(80), default='sface-2021dec')
    person = relationship('Person')
    operator = relationship('User')

class RecognitionLog(Base):
    __tablename__ = 'recognition_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey('people.id', ondelete='SET NULL'), nullable=True, index=True)
    recognized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    confidence_score: Mapped[float | None] = mapped_column()
    recognition_status: Mapped[str] = mapped_column(String(20))
    operator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    person = relationship('Person')
    operator = relationship('User')

class Session(Base):
    __tablename__ = 'attendance_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    group_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    teacher = relationship('User')

class Shift(Base):
    __tablename__ = 'shifts'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    grace_minutes: Mapped[int] = mapped_column(Integer, default=5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class AttendanceEvent(Base):
    __tablename__ = 'attendance_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey('people.id'), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    source: Mapped[str] = mapped_column(String(30), default='manual')
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey('attendance_sessions.id'), nullable=True)
    shift_id: Mapped[int | None] = mapped_column(ForeignKey('shifts.id'), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), default='local')
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    person = relationship('Person')
    session = relationship('Session')
    shift = relationship('Shift')
    __table_args__ = (
        Index('ix_event_person_time', 'person_id', 'occurred_at'),
        Index('uq_education_attendance_once', 'person_id', 'session_id', unique=True,
              sqlite_where=text("session_id IS NOT NULL AND event_type IN ('present','late')"),
              postgresql_where=text("session_id IS NOT NULL AND event_type IN ('present','late')")),
        Index('uq_shift_checkin_once', 'person_id', 'shift_id', unique=True,
              sqlite_where=text("shift_id IS NOT NULL AND event_type IN ('check_in','late')"),
              postgresql_where=text("shift_id IS NOT NULL AND event_type IN ('check_in','late')")),
        Index('uq_shift_checkout_once', 'person_id', 'shift_id', unique=True,
              sqlite_where=text("shift_id IS NOT NULL AND event_type = 'check_out'"),
              postgresql_where=text("shift_id IS NOT NULL AND event_type = 'check_out'")),
    )

class Correction(Base):
    __tablename__ = 'corrections'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey('attendance_events.id'))
    before_value: Mapped[str] = mapped_column(Text)
    after_value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (Index('ix_correction_event_time', 'event_id', 'created_at'),)

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(160))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

class SyncReceipt(Base):
    __tablename__ = 'sync_receipts'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_uid: Mapped[str] = mapped_column(String(64), unique=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
