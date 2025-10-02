
from pydantic import BaseModel, Field
from typing import Optional, List

class LoginRequest(BaseModel):
    username: str
    password: str

class ClientDevice(BaseModel):
    host: Optional[str] = None
    mac_list: Optional[list[str]] = None
    ip_list: Optional[list[str]] = None
    last_seen: Optional[str] = None
    client_version: Optional[str] = None

class ClientInfo(BaseModel):
    description: Optional[str] = None
    is_active: bool = True
    devices: list[ClientDevice] = Field(default_factory=list)

class PrintCheckResponse(BaseModel):
    allow: bool
    status: str
    duplicate_kind: Optional[str] = None
    print_count: int = 0
    tracking_no: Optional[str] = None
    order_id: Optional[str] = None
