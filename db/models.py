from sqlalchemy import Column, BigInteger, String, ForeignKey, Boolean, Table
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    
    sessions = relationship("TelegramSession", back_populates="user", cascade="all, delete-orphan")
    proxies = relationship("ProxySettings", back_populates="user", cascade="all, delete-orphan")
    flows = relationship("Flow", back_populates="user", cascade="all, delete-orphan")

class TelegramSession(Base):
    __tablename__ = "telegram_sessions"
    
    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    api_id = Column(BigInteger, nullable=False)
    api_hash = Column(String, nullable=False)
    session_file = Column(String, nullable=False)
    proxy_id = Column(BigInteger, ForeignKey("proxy_settings.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=False)  # Исправлено
    
    user = relationship("User", back_populates="sessions")
    proxy = relationship("ProxySettings", back_populates="sessions", foreign_keys=[proxy_id])

class ProxySettings(Base):
    __tablename__ = "proxy_settings"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    proxy_type = Column(String, nullable=False)
    proxy_host = Column(String, nullable=False)
    proxy_port = Column(BigInteger, nullable=False)
    proxy_login = Column(String, nullable=True)
    proxy_password = Column(String, nullable=True)

    user = relationship("User", back_populates="proxies")
    sessions = relationship("TelegramSession", back_populates="proxy")

flow_sessions_table = Table(
    "flow_sessions",
    Base.metadata,
    Column("flow_id", BigInteger, ForeignKey("flows.id"), primary_key=True),
    Column("session_id", BigInteger, ForeignKey("telegram_sessions.id"), primary_key=True)
)

class Flow(Base):
    __tablename__ = "flows"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    
    user = relationship("User", back_populates="flows")
    sessions = relationship("TelegramSession", secondary=flow_sessions_table, back_populates="flows")

TelegramSession.flows = relationship("Flow", secondary=flow_sessions_table, back_populates="sessions")

