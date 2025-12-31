from fastapi import APIRouter, HTTPException, Depends
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from datetime import datetime, date
import asyncio

from ..db.database import get_db_session
from ..db.models import Venda, ItemVenda, Produto

router = APIRouter(prefix="/api/metricas", tags=["metricas"]) 

# Cache em memória simples para suavizar cold start (TTL curto)
_metrics_cache = {
    "vendas_dia": {"value": None, "ts": 0.0},
    "vendas_mes": {"value": None, "ts": 0.0},
}
_cache_ttl_seconds = 15
_cache_lock = asyncio.Lock()

def _now_ts() -> float:
    return datetime.utcnow().timestamp()

@router.get("/vendas-dia")
async def vendas_dia(
    data: str | None = Query(default=None, description="Data no formato YYYY-MM-DD (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session)
):
    """Retorna o total de vendas (não canceladas) do dia informado (ou dia atual)."""
    # Data alvo: usar a recebida do cliente ou o dia do servidor
    try:
        alvo = date.fromisoformat(data) if data else date.today()
    except Exception:
        alvo = date.today()
    # Servir cache se fresco
    async with _cache_lock:
        if _metrics_cache["vendas_dia"]["value"] is not None and (_now_ts() - _metrics_cache["vendas_dia"]["ts"]) < _cache_ttl_seconds:
            return {"data": str(alvo), "total": float(_metrics_cache["vendas_dia"]["value"]) }

    stmt = select(
        func.coalesce(
            func.sum(func.coalesce(Venda.total, 0.0) - func.coalesce(Venda.desconto, 0.0)),
            0.0,
        )
    ).where(
        Venda.cancelada == False,
        func.date(Venda.created_at) == alvo
    )
    # Tentativa principal + 1 retry leve
    for attempt in range(2):
        try:
            result = await db.execute(stmt)
            total = float(result.scalar() or 0.0)
            async with _cache_lock:
                _metrics_cache["vendas_dia"] = {"value": total, "ts": _now_ts()}
            return {"data": str(alvo), "total": total}
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue
            # Fallback: servir cache antigo se existir, senão 0
            async with _cache_lock:
                cached = _metrics_cache["vendas_dia"]["value"]
            return {"data": str(alvo), "total": float(cached or 0.0), "warning": "cached"}

@router.get("/vendas-mes")
async def vendas_mes(
    ano_mes: str | None = Query(default=None, description="Ano-mês no formato YYYY-MM (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session)
):
    """Retorna o total de vendas (não canceladas) do mês informado (ou mês atual)."""
    try:
        if ano_mes:
            try:
                ano, mes = map(int, ano_mes.split("-"))
                primeiro_dia = date(ano, mes, 1)
            except Exception:
                dnow = datetime.utcnow()
                primeiro_dia = date(dnow.year, dnow.month, 1)
        else:
            dnow = datetime.utcnow()
            primeiro_dia = date(dnow.year, dnow.month, 1)
        # próximo mês
        proximo_mes = date(
            primeiro_dia.year + (1 if primeiro_dia.month == 12 else 0),
            1 if primeiro_dia.month == 12 else primeiro_dia.month + 1,
            1
        )
        # Intervalo [primeiro_dia, proximo_mes)
        stmt = select(
            func.coalesce(
                func.sum(func.coalesce(Venda.total, 0.0) - func.coalesce(Venda.desconto, 0.0)),
                0.0,
            )
        ).where(
            Venda.cancelada == False,
            Venda.created_at >= primeiro_dia,
            Venda.created_at < proximo_mes
        )
        # Servir cache se fresco
        async with _cache_lock:
            if _metrics_cache["vendas_mes"]["value"] is not None and (_now_ts() - _metrics_cache["vendas_mes"]["ts"]) < _cache_ttl_seconds:
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": float(_metrics_cache["vendas_mes"]["value"]) }

        for attempt in range(2):
            try:
                result = await db.execute(stmt)
                total = float(result.scalar() or 0.0)
                async with _cache_lock:
                    _metrics_cache["vendas_mes"] = {"value": total, "ts": _now_ts()}
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": total}
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                async with _cache_lock:
                    cached = _metrics_cache["vendas_mes"]["value"]
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": float(cached or 0.0), "warning": "cached"}
    except Exception:
        # Falha inesperada: retornar cache ou zero
        async with _cache_lock:
            cached = _metrics_cache["vendas_mes"]["value"]
        return {"ano_mes": datetime.utcnow().strftime("%Y-%m"), "total": float(cached or 0.0), "warning": "cached"}


@router.get("/lucro-dia")
async def lucro_dia(
    data: str | None = Query(default=None, description="Data no formato YYYY-MM-DD (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        alvo = date.fromisoformat(data) if data else date.today()
    except Exception:
        alvo = date.today()

    qtd_expr = case(
        (func.coalesce(ItemVenda.peso_kg, 0.0) > 0.0, func.coalesce(ItemVenda.peso_kg, 0.0)),
        else_=func.coalesce(ItemVenda.quantidade, 0),
    )
    custo_unit_expr = func.nullif(func.coalesce(ItemVenda.preco_custo_unitario, 0.0), 0.0)
    custo_unit_final = func.coalesce(custo_unit_expr, func.coalesce(Produto.preco_custo, 0.0))

    stmt = (
        select(
            func.coalesce(
                func.sum((func.coalesce(ItemVenda.preco_unitario, 0.0) - custo_unit_final) * qtd_expr),
                0.0,
            )
        )
        .select_from(ItemVenda)
        .join(Venda, ItemVenda.venda_id == Venda.id)
        .join(Produto, ItemVenda.produto_id == Produto.id)
        .where(Venda.cancelada == False, func.date(Venda.created_at) == alvo)
    )

    try:
        result = await db.execute(stmt)
        total = float(result.scalar() or 0.0)
        return {"data": str(alvo), "total": total}
    except Exception:
        return {"data": str(alvo), "total": 0.0}


@router.get("/lucro-mes")
async def lucro_mes(
    ano_mes: str | None = Query(default=None, description="Ano-mês no formato YYYY-MM (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        if ano_mes:
            try:
                ano, mes = map(int, ano_mes.split("-"))
                primeiro_dia = date(ano, mes, 1)
            except Exception:
                dnow = datetime.utcnow()
                primeiro_dia = date(dnow.year, dnow.month, 1)
        else:
            dnow = datetime.utcnow()
            primeiro_dia = date(dnow.year, dnow.month, 1)

        proximo_mes = date(
            primeiro_dia.year + (1 if primeiro_dia.month == 12 else 0),
            1 if primeiro_dia.month == 12 else primeiro_dia.month + 1,
            1,
        )

        qtd_expr = case(
            (func.coalesce(ItemVenda.peso_kg, 0.0) > 0.0, func.coalesce(ItemVenda.peso_kg, 0.0)),
            else_=func.coalesce(ItemVenda.quantidade, 0),
        )
        custo_unit_expr = func.nullif(func.coalesce(ItemVenda.preco_custo_unitario, 0.0), 0.0)
        custo_unit_final = func.coalesce(custo_unit_expr, func.coalesce(Produto.preco_custo, 0.0))

        stmt = (
            select(
                func.coalesce(
                    func.sum((func.coalesce(ItemVenda.preco_unitario, 0.0) - custo_unit_final) * qtd_expr),
                    0.0,
                )
            )
            .select_from(ItemVenda)
            .join(Venda, ItemVenda.venda_id == Venda.id)
            .join(Produto, ItemVenda.produto_id == Produto.id)
            .where(
                Venda.cancelada == False,
                Venda.created_at >= primeiro_dia,
                Venda.created_at < proximo_mes,
            )
        )

        result = await db.execute(stmt)
        total = float(result.scalar() or 0.0)
        return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": total}
    except Exception:
        return {"ano_mes": datetime.utcnow().strftime("%Y-%m"), "total": 0.0}

@router.get("/estoque")
async def metricas_estoque(
    db: AsyncSession = Depends(get_db_session)
):
    """Retorna métricas de estoque: valor_estoque (custo), valor_potencial (venda) e lucro_potencial.

    Fórmulas:
    - valor_estoque = SUM(estoque * preco_custo) WHERE ativo = true
    - valor_potencial = SUM(estoque * preco_venda) WHERE ativo = true
    - lucro_potencial = valor_potencial - valor_estoque
    """
    try:
        stmt_custo = select(func.coalesce(func.sum(Produto.estoque * Produto.preco_custo), 0.0)).where(Produto.ativo == True)
        stmt_venda = select(func.coalesce(func.sum(Produto.estoque * Produto.preco_venda), 0.0)).where(Produto.ativo == True)

        result_custo = await db.execute(stmt_custo)
        result_venda = await db.execute(stmt_venda)

        valor_estoque = float(result_custo.scalar() or 0.0)
        valor_potencial = float(result_venda.scalar() or 0.0)
        lucro_potencial = float(valor_potencial - valor_estoque)

        return {
            "valor_estoque": valor_estoque,
            "valor_potencial": valor_potencial,
            "lucro_potencial": lucro_potencial,
        }
    except Exception as e:
        # Em falha, retornar zeros para não quebrar o cliente
        return {
            "valor_estoque": 0.0,
            "valor_potencial": 0.0,
            "lucro_potencial": 0.0,
            "warning": str(e)
        }
