from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.db.database import get_db_session
from app.core.deps import get_current_admin_user
from app.core.security import get_password_hash

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/reset-dados")
async def reset_dados_online(
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    """Reseta TODO o banco de dados online, apagando todos os registros.

    Somente administradores podem executar esta operação.
    """
    try:
        # Truncar todas as tabelas do schema public (preserva estrutura)
        # Evitar truncar alembic_version caso exista
        result = await db.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename <> 'alembic_version'
                """
            )
        )
        tables = [r[0] for r in (result.fetchall() or [])]

        for table in tables:
            await db.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE;'))

        # Recriar admin padrão
        senha_hash = get_password_hash("842384")
        await db.execute(
            text(
                """
                INSERT INTO usuarios (
                    nome, usuario, senha_hash,
                    is_admin, ativo,
                    nivel, salario,
                    pode_abastecer, pode_gerenciar_despesas, pode_fazer_devolucao
                )
                VALUES (
                    :nome, :usuario, :senha_hash,
                    true, true,
                    2, 0.0,
                    true, true, true
                )
                ON CONFLICT (usuario)
                DO UPDATE SET
                    nome = EXCLUDED.nome,
                    senha_hash = EXCLUDED.senha_hash,
                    is_admin = true,
                    ativo = true,
                    nivel = 2,
                    salario = 0.0,
                    pode_abastecer = true,
                    pode_gerenciar_despesas = true,
                    pode_fazer_devolucao = true
                """
            ),
            {
                "nome": "Neotrix Tecnologias",
                "usuario": "Neotrix",
                "senha_hash": senha_hash,
            },
        )

        await db.commit()

        return {
            "status": "ok",
            "message": "Banco de dados online foi totalmente resetado (todas as tabelas truncadas) e o admin padrão foi recriado.",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao resetar banco de dados: {str(e)}",
        )
