from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from typing import List
import uuid
from datetime import datetime

from ..db.database import get_db_session
from ..db.models import User
from app.core.realtime import manager as realtime_manager
from ..schemas.usuario import UsuarioCreate, UsuarioUpdate, UsuarioResponse
from werkzeug.security import generate_password_hash


def _looks_like_hash(value: str) -> bool:
    """Retorna True se a string parece um hash já pronto (pbkdf2/bcrypt)."""
    if not value:
        return False
    v = str(value)
    return v.startswith("pbkdf2:") or v.startswith("$2a$") or v.startswith("$2b$") or v.startswith("$2y$")

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])

@router.get("/", response_model=List[dict])
async def listar_usuarios(db: AsyncSession = Depends(get_db_session)):
    """Lista todos os usuários."""
    try:
        result = await db.execute(select(User).where(User.ativo == True))
        usuarios = result.scalars().all()
        
        # Converter para dict com uuid
        usuarios_dict = []
        for usuario in usuarios:
            usuario_dict = {
                'uuid': str(usuario.id),
                'id': str(usuario.id),
                'nome': usuario.nome,
                'usuario': usuario.usuario,
                'is_admin': usuario.is_admin,
                'ativo': usuario.ativo,
                'nivel': usuario.nivel,
                'salario': usuario.salario,
                'pode_abastecer': usuario.pode_abastecer,
                'pode_gerenciar_despesas': usuario.pode_gerenciar_despesas,
                'pode_fazer_devolucao': getattr(usuario, 'pode_fazer_devolucao', False),
                'created_at': usuario.created_at.isoformat(),
                'updated_at': usuario.updated_at.isoformat()
            }
            usuarios_dict.append(usuario_dict)
        
        return usuarios_dict
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar usuários: {str(e)}")

@router.get("/desativados", response_model=List[dict])
async def listar_usuarios_desativados(db: AsyncSession = Depends(get_db_session)):
    """Lista todos os usuários desativados (ativo = False)."""
    try:
        result = await db.execute(select(User).where(User.ativo == False))
        usuarios = result.scalars().all()

        usuarios_dict = []
        for usuario in usuarios:
            usuario_dict = {
                'uuid': str(usuario.id),
                'id': str(usuario.id),
                'nome': usuario.nome,
                'usuario': usuario.usuario,
                'is_admin': usuario.is_admin,
                'ativo': usuario.ativo,
                'nivel': usuario.nivel,
                'salario': usuario.salario,
                'pode_abastecer': usuario.pode_abastecer,
                'pode_gerenciar_despesas': usuario.pode_gerenciar_despesas,
                'created_at': usuario.created_at.isoformat(),
                'updated_at': usuario.updated_at.isoformat()
            }
            usuarios_dict.append(usuario_dict)

        return usuarios_dict
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar usuários desativados: {str(e)}")

@router.get("/{usuario_id}", response_model=UsuarioResponse)
async def obter_usuario(usuario_id: str, db: AsyncSession = Depends(get_db_session)):
    """Obtém um usuário específico por UUID."""
    try:
        # Tentar buscar por UUID primeiro
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario = result.scalar_one_or_none()
        
        if not usuario:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
        return usuario
    except ValueError:
        # Se não for UUID válido, retornar erro
        raise HTTPException(status_code=400, detail="ID de usuário inválido")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter usuário: {str(e)}")

@router.post("/", response_model=UsuarioResponse)
async def criar_usuario(usuario: UsuarioCreate, db: AsyncSession = Depends(get_db_session)):
    """Cria um novo usuário."""
    try:
        # Verificar se já existe usuário com mesmo nome de usuário
        result = await db.execute(select(User).where(User.usuario == usuario.usuario))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Nome de usuário já existe")
        
        # Criar novo usuário
        usuario_uuid = uuid.uuid4()
        if hasattr(usuario, 'uuid') and usuario.uuid:
            try:
                usuario_uuid = uuid.UUID(usuario.uuid)
            except ValueError:
                usuario_uuid = uuid.uuid4()

        # Verificar duplicidade de UUID (id) antes de inserir
        existing_by_id = await db.execute(select(User).where(User.id == str(usuario_uuid)))
        if existing_by_id.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Usuário já existe (mesmo id)")
        
        # Se a senha já vier hasheada (ex.: sync offline), usar diretamente; caso contrário, gerar hash PBKDF2
        senha_hash = usuario.senha if _looks_like_hash(usuario.senha) else generate_password_hash(usuario.senha)

        novo_usuario = User(
            id=usuario_uuid,
            nome=usuario.nome,
            usuario=usuario.usuario,
            senha_hash=senha_hash,
            is_admin=usuario.is_admin,
            ativo=True,
            nivel=usuario.nivel,
            salario=usuario.salario,
            pode_abastecer=usuario.pode_abastecer,
            pode_gerenciar_despesas=usuario.pode_gerenciar_despesas,
            pode_fazer_devolucao=usuario.pode_fazer_devolucao
        )
        
        db.add(novo_usuario)
        await db.commit()
        await db.refresh(novo_usuario)

        # Broadcast realtime: usuario criado
        try:
            await realtime_manager.broadcast("usuario.created", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(novo_usuario.id),
                    "nome": novo_usuario.nome,
                    "usuario": novo_usuario.usuario,
                    "is_admin": bool(novo_usuario.is_admin),
                    "ativo": bool(novo_usuario.ativo),
                    "pode_abastecer": bool(getattr(novo_usuario, 'pode_abastecer', False)),
                    "pode_gerenciar_despesas": bool(getattr(novo_usuario, 'pode_gerenciar_despesas', False)),
                    "pode_fazer_devolucao": bool(getattr(novo_usuario, 'pode_fazer_devolucao', False)),
                    "updated_at": novo_usuario.updated_at.isoformat() if getattr(novo_usuario, 'updated_at', None) else None,
                }
            })
        except Exception:
            pass

        return novo_usuario
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar usuário: {str(e)}")

@router.put("/{usuario_id}", response_model=UsuarioResponse)
async def atualizar_usuario(usuario_id: str, usuario: UsuarioUpdate, db: AsyncSession = Depends(get_db_session)):
    """Atualiza um usuário existente."""
    try:
        # Buscar usuário existente
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario_existente = result.scalar_one_or_none()
        
        if not usuario_existente:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
        # Proteger usuário admin padrão contra desativação via API
        if (usuario_existente.usuario == "admin" or usuario_existente.is_admin) and hasattr(usuario, 'ativo') and usuario.ativo is False:
            raise HTTPException(status_code=400, detail="Não é permitido desativar o usuário administrador padrão")
        
        # Atualizar campos
        update_data = {}
        if usuario.nome is not None:
            update_data['nome'] = usuario.nome
        if usuario.usuario is not None:
            update_data['usuario'] = usuario.usuario
        if usuario.senha is not None:
            # Se já for hash, salvar direto; senão, gerar hash PBKDF2
            update_data['senha_hash'] = (
                usuario.senha if _looks_like_hash(usuario.senha) else generate_password_hash(usuario.senha)
            )
        if usuario.is_admin is not None:
            update_data['is_admin'] = usuario.is_admin
        if hasattr(usuario, 'ativo') and usuario.ativo is not None:
            update_data['ativo'] = usuario.ativo
        if hasattr(usuario, 'nivel') and usuario.nivel is not None:
            update_data['nivel'] = usuario.nivel
        if hasattr(usuario, 'salario') and usuario.salario is not None:
            update_data['salario'] = usuario.salario
        if hasattr(usuario, 'pode_abastecer') and usuario.pode_abastecer is not None:
            update_data['pode_abastecer'] = usuario.pode_abastecer
        if hasattr(usuario, 'pode_gerenciar_despesas') and usuario.pode_gerenciar_despesas is not None:
            update_data['pode_gerenciar_despesas'] = usuario.pode_gerenciar_despesas
        if hasattr(usuario, 'pode_fazer_devolucao') and usuario.pode_fazer_devolucao is not None:
            update_data['pode_fazer_devolucao'] = usuario.pode_fazer_devolucao
        
        update_data['updated_at'] = datetime.utcnow()
        
        if update_data:
            await db.execute(
                update(User).where(User.id == usuario_id).values(**update_data)
            )
        await db.commit()
        
        # Retornar usuário atualizado
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario_atualizado = result.scalar_one()

        # Broadcast realtime: usuario atualizado
        try:
            await realtime_manager.broadcast("usuario.updated", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(usuario_atualizado.id),
                    "nome": usuario_atualizado.nome,
                    "usuario": usuario_atualizado.usuario,
                    "is_admin": bool(usuario_atualizado.is_admin),
                    "ativo": bool(usuario_atualizado.ativo),
                    "pode_abastecer": bool(getattr(usuario_atualizado, 'pode_abastecer', False)),
                    "pode_gerenciar_despesas": bool(getattr(usuario_atualizado, 'pode_gerenciar_despesas', False)),
                    "pode_fazer_devolucao": bool(getattr(usuario_atualizado, 'pode_fazer_devolucao', False)),
                    "updated_at": usuario_atualizado.updated_at.isoformat() if getattr(usuario_atualizado, 'updated_at', None) else None,
                }
            })
        except Exception:
            pass

        return usuario_atualizado
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar usuário: {str(e)}")

@router.delete("/{usuario_id}")
async def deletar_usuario(usuario_id: str, db: AsyncSession = Depends(get_db_session)):
    """Deleta um usuário (soft delete)."""
    try:
        # Buscar usuário existente
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario_existente = result.scalar_one_or_none()
        
        if not usuario_existente:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
        # Impedir exclusão do administrador padrão
        if usuario_existente.usuario == "admin":
            raise HTTPException(status_code=400, detail="Não é permitido excluir o usuário administrador padrão")
        
        # Opcional: se for o único admin ativo, também impedir exclusão
        if usuario_existente.is_admin:
            result_admins = await db.execute(
                select(func.count()).select_from(User).where(User.is_admin == True, User.ativo == True)
            )
            total_admins_ativos = result_admins.scalar_one() or 0
            if total_admins_ativos <= 1:
                raise HTTPException(status_code=400, detail="Não é permitido excluir o único administrador ativo")
        
        # Soft delete
        await db.execute(
            update(User)
            .where(User.id == usuario_id)
            .values(ativo=False, updated_at=datetime.utcnow())
        )
        await db.commit()

        # Broadcast realtime: usuario deletado
        try:
            await realtime_manager.broadcast("usuario.deleted", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(usuario_id),
                }
            })
        except Exception:
            pass

        return {"message": "Usuário deletado com sucesso"}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao deletar usuário: {str(e)}")

@router.put("/{usuario_id}/ativar", response_model=UsuarioResponse)
async def ativar_usuario(usuario_id: str, db: AsyncSession = Depends(get_db_session)):
    """Ativa um usuário (ativo = True)."""
    try:
        # Verificar existência
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario_existente = result.scalar_one_or_none()
        if not usuario_existente:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        # Ativar
        await db.execute(
            update(User)
            .where(User.id == usuario_id)
            .values(ativo=True, updated_at=datetime.utcnow())
        )
        await db.commit()

        # Retornar usuário atualizado
        result = await db.execute(select(User).where(User.id == usuario_id))
        usuario_ativado = result.scalar_one()

        # Broadcast realtime: usuario ativado
        try:
            await realtime_manager.broadcast("usuario.activated", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(usuario_ativado.id),
                    "ativo": True,
                    "updated_at": usuario_ativado.updated_at.isoformat() if getattr(usuario_ativado, 'updated_at', None) else None,
                }
            })
        except Exception:
            pass

        return usuario_ativado
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao ativar usuário: {str(e)}")
