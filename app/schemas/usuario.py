from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
import uuid

class UsuarioBase(BaseModel):
    nome: str = Field(..., min_length=1, max_length=100)
    usuario: str = Field(..., min_length=3, max_length=50)
    is_admin: bool = False
    nivel: int = 1
    salario: float = 0.0
    pode_abastecer: bool = False
    pode_gerenciar_despesas: bool = False
    pode_fazer_devolucao: bool = False

class UsuarioCreate(UsuarioBase):
    senha: str = Field(..., min_length=1)
    uuid: Optional[str] = None

class UsuarioUpdate(BaseModel):
    nome: Optional[str] = Field(None, min_length=1, max_length=100)
    usuario: Optional[str] = Field(None, min_length=3, max_length=50)
    senha: Optional[str] = None
    is_admin: Optional[bool] = None
    nivel: Optional[int] = None
    salario: Optional[float] = None
    pode_abastecer: Optional[bool] = None
    pode_gerenciar_despesas: Optional[bool] = None
    pode_fazer_devolucao: Optional[bool] = None
    ativo: Optional[bool] = None

class UsuarioResponse(UsuarioBase):
    id: str
    ativo: bool
    created_at: datetime
    updated_at: datetime

    @field_validator('id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }
