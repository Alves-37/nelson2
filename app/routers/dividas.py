from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from app.db.database import get_db_session
from app.db.models import Divida, ItemDivida, PagamentoDivida, Produto, Cliente, User, Venda, ItemVenda


router = APIRouter(prefix="/api/dividas", tags=["dividas"])


class ItemDividaIn(BaseModel):
    produto_id: str
    quantidade: float
    preco_unitario: float
    subtotal: float


class DividaCreate(BaseModel):
    id_local: Optional[int] = None
    cliente_id: Optional[str] = None
    usuario_id: Optional[str] = None
    observacao: Optional[str] = None
    desconto_aplicado: float = 0.0
    percentual_desconto: float = 0.0
    itens: List[ItemDividaIn]


class PagamentoDividaIn(BaseModel):
    valor: float
    forma_pagamento: str
    usuario_id: Optional[str] = None


class DividaSyncRequest(BaseModel):
    """Payload para sincronização em lote de dívidas.

    Mantém o mesmo formato de DividaCreate, mas em lista no campo data,
    para permitir uso por ferramentas de sync genéricas.
    """
    data: List[DividaCreate]


class DividaOut(BaseModel):
    id: uuid.UUID
    id_local: Optional[int]
    cliente_id: Optional[uuid.UUID]
    usuario_id: Optional[uuid.UUID]
    cliente_nome: Optional[str] = None
    usuario_nome: Optional[str] = None
    data_divida: datetime
    valor_total: float
    valor_original: float
    desconto_aplicado: float
    percentual_desconto: float
    valor_pago: float
    status: str
    observacao: Optional[str] = None

    class Config:
        from_attributes = True


class ItemDividaOut(BaseModel):
    produto_id: uuid.UUID
    produto_nome: Optional[str] = None
    quantidade: float
    preco_unitario: float
    subtotal: float

    class Config:
        from_attributes = True


class DividaDetailOut(DividaOut):
    itens: List[ItemDividaOut] = []


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _to_divida_out(divida: Divida, cliente_nome: Optional[str] = None, usuario_nome: Optional[str] = None) -> DividaOut:
    try:
        return DividaOut(
            id=getattr(divida, 'id'),
            id_local=getattr(divida, 'id_local', None),
            cliente_id=getattr(divida, 'cliente_id', None),
            usuario_id=getattr(divida, 'usuario_id', None),
            cliente_nome=cliente_nome,
            usuario_nome=usuario_nome,
            data_divida=getattr(divida, 'data_divida'),
            valor_total=float(getattr(divida, 'valor_total', 0.0) or 0.0),
            valor_original=float(getattr(divida, 'valor_original', 0.0) or 0.0),
            desconto_aplicado=float(getattr(divida, 'desconto_aplicado', 0.0) or 0.0),
            percentual_desconto=float(getattr(divida, 'percentual_desconto', 0.0) or 0.0),
            valor_pago=float(getattr(divida, 'valor_pago', 0.0) or 0.0),
            status=str(getattr(divida, 'status', '') or ''),
            observacao=getattr(divida, 'observacao', None),
        )
    except Exception as e:
        # Em último caso, propagar erro controlado
        raise HTTPException(status_code=500, detail=f"Falha ao construir resposta da dívida: {e}")

def _to_divida_out_from_snapshot(data: dict) -> DividaOut:
    try:
        return DividaOut(
            id=data.get('id'),
            id_local=data.get('id_local'),
            cliente_id=data.get('cliente_id'),
            usuario_id=data.get('usuario_id'),
            cliente_nome=data.get('cliente_nome'),
            usuario_nome=data.get('usuario_nome'),
            data_divida=data.get('data_divida'),
            valor_total=float(data.get('valor_total') or 0.0),
            valor_original=float(data.get('valor_original') or 0.0),
            desconto_aplicado=float(data.get('desconto_aplicado') or 0.0),
            percentual_desconto=float(data.get('percentual_desconto') or 0.0),
            valor_pago=float(data.get('valor_pago') or 0.0),
            status=str(data.get('status') or ''),
            observacao=data.get('observacao'),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao construir resposta da dívida: {e}")

@router.post("/", response_model=DividaOut, status_code=201)
async def criar_divida(payload: DividaCreate, db: AsyncSession = Depends(get_db_session)):
    """Cria uma nova dívida com itens, alinhada ao modelo local do PDV3."""
    if not payload.itens:
        raise HTTPException(status_code=400, detail="É necessário informar pelo menos um item na dívida.")

    try:
        # Converter IDs para UUIDs
        cliente_uuid = _parse_uuid(payload.cliente_id)
        usuario_uuid = _parse_uuid(payload.usuario_id)

        # Calcular valores
        valor_original = sum(float(i.subtotal) for i in payload.itens)
        desconto_aplicado = float(payload.desconto_aplicado or 0.0)
        if payload.percentual_desconto and payload.percentual_desconto > 0:
            desconto_aplicado = valor_original * (float(payload.percentual_desconto) / 100.0)
        valor_total = max(0.0, valor_original - desconto_aplicado)

        nova_divida = Divida(
            id_local=payload.id_local,
            cliente_id=cliente_uuid,
            usuario_id=usuario_uuid,
            valor_total=valor_total,
            valor_original=valor_original,
            desconto_aplicado=desconto_aplicado,
            percentual_desconto=float(payload.percentual_desconto or 0.0),
            valor_pago=0.0,
            status="Pendente",
            observacao=payload.observacao,
        )

        db.add(nova_divida)
        await db.flush()  # obter ID

        # Criar itens da dívida
        for item in payload.itens:
            produto_uuid = _parse_uuid(item.produto_id)
            if not produto_uuid:
                raise HTTPException(status_code=400, detail=f"produto_id inválido: {item.produto_id}")

            # Verificar se produto existe
            result_prod = await db.execute(select(Produto).where(Produto.id == produto_uuid))
            if not result_prod.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {item.produto_id}")

            db.add(
                ItemDivida(
                    divida_id=nova_divida.id,
                    produto_id=produto_uuid,
                    quantidade=float(item.quantidade),
                    preco_unitario=float(item.preco_unitario),
                    subtotal=float(item.subtotal),
                )
            )

        await db.commit()
        await db.refresh(nova_divida)

        # Buscar nomes de cliente e usuário sem lazy loading
        cli_nome = None
        usr_nome = None
        try:
            if nova_divida.cliente_id:
                r = await db.execute(select(Cliente.nome).where(Cliente.id == nova_divida.cliente_id))
                cli_nome = r.scalar_one_or_none()
        except Exception:
            cli_nome = None
        try:
            if nova_divida.usuario_id:
                r = await db.execute(select(User.nome).where(User.id == nova_divida.usuario_id))
                usr_nome = r.scalar_one_or_none()
        except Exception:
            usr_nome = None

        return _to_divida_out(nova_divida, cliente_nome=cli_nome, usuario_nome=usr_nome)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar dívida: {str(e)}")


@router.get("/id/{divida_id}", response_model=DividaDetailOut)
async def obter_divida(divida_id: str, db: AsyncSession = Depends(get_db_session)):
    try:
        divida_uuid = _parse_uuid(divida_id)
        if not divida_uuid:
            raise HTTPException(status_code=400, detail="ID de dívida inválido.")

        result = await db.execute(select(Divida).where(Divida.id == divida_uuid))
        divida = result.scalar_one_or_none()
        if not divida:
            raise HTTPException(status_code=404, detail="Dívida não encontrada.")

        # Carregar itens com nome do produto
        itens_result = await db.execute(
            select(ItemDivida, Produto.nome.label("produto_nome"))
            .join(Produto, ItemDivida.produto_id == Produto.id, isouter=True)
            .where(ItemDivida.divida_id == divida.id)
        )
        itens_rows = itens_result.all()
        itens_out: List[ItemDividaOut] = []
        for it, prod_nome in itens_rows:
            try:
                obj = ItemDividaOut(
                    produto_id=it.produto_id,
                    produto_nome=prod_nome,
                    quantidade=float(it.quantidade or 0.0),
                    preco_unitario=float(it.preco_unitario or 0.0),
                    subtotal=float(it.subtotal or 0.0),
                )
                itens_out.append(obj)
            except Exception:
                continue

        # Buscar nomes de cliente e usuário sem lazy loading
        cli_nome = None
        usr_nome = None
        try:
            if divida.cliente_id:
                r = await db.execute(select(Cliente.nome).where(Cliente.id == divida.cliente_id))
                cli_nome = r.scalar_one_or_none()
        except Exception:
            cli_nome = None
        try:
            if divida.usuario_id:
                r = await db.execute(select(User.nome).where(User.id == divida.usuario_id))
                usr_nome = r.scalar_one_or_none()
        except Exception:
            usr_nome = None

        base = _to_divida_out(divida, cliente_nome=cli_nome, usuario_nome=usr_nome)
        return DividaDetailOut(**base.model_dump(), itens=itens_out)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter dívida: {str(e)}")

@router.get("/", response_model=List[DividaOut])
async def listar_dividas(
    cliente_id: Optional[str] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Lista todas as dívidas, com filtros opcionais por cliente e status.

    - Se status for informado (ex.: 'Pendente', 'Parcial', 'Quitado'), filtra por igualdade.
    - Se cliente_id (UUID) for informado, filtra por cliente.
    """
    try:
        stmt = (
            select(
                Divida,
                Cliente.nome.label("cliente_nome"),
                User.nome.label("usuario_nome"),
            )
            .join(Cliente, Divida.cliente_id == Cliente.id, isouter=True)
            .join(User, Divida.usuario_id == User.id, isouter=True)
        )

        cliente_uuid = _parse_uuid(cliente_id)
        if cliente_uuid:
            stmt = stmt.where(Divida.cliente_id == cliente_uuid)

        if status:
            stmt = stmt.where(Divida.status == status)

        result = await db.execute(stmt.order_by(Divida.data_divida.desc()))
        rows = result.all()

        resposta: list[DividaOut] = []
        for divida, cli_nome, usr_nome in rows:
            try:
                out = _to_divida_out(divida, cliente_nome=cli_nome, usuario_nome=usr_nome)
            except Exception:
                out = _to_divida_out(divida, cliente_nome=cli_nome, usuario_nome=None)
            resposta.append(out)
        return resposta
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar dívidas: {str(e)}")

@router.post("/sync")
async def sync_dividas(payload: DividaSyncRequest, db: AsyncSession = Depends(get_db_session)):
    """Sincroniza dívidas em lote a partir do PDV, usando id_local como chave.

    Para cada registro em payload.data:
    - Se existir uma dívida com mesmo id_local, é ignorada (idempotente).
    - Caso contrário, é criada usando a mesma lógica da rota criar_divida.
    """
    if not payload.data:
        return {"status": "ok", "created": 0, "skipped": 0, "errors": []}

    created = 0
    skipped = 0
    errors: List[dict] = []

    for idx, item in enumerate(payload.data):
        try:
            # Verificar se já existe dívida com mesmo id_local
            if item.id_local is not None:
                stmt = select(Divida).where(Divida.id_local == item.id_local)
                result = await db.execute(stmt)
                existente = result.scalar_one_or_none()
                if existente:
                    skipped += 1
                    continue

            # Reusar lógica básica de criação (sem duplicar validações de forma exata)
            if not item.itens:
                raise HTTPException(status_code=400, detail="Dívida sem itens não pode ser sincronizada.")

            cliente_uuid = _parse_uuid(item.cliente_id)
            usuario_uuid = _parse_uuid(item.usuario_id)

            valor_original = sum(float(i.subtotal) for i in item.itens)
            desconto_aplicado = float(item.desconto_aplicado or 0.0)
            if item.percentual_desconto and item.percentual_desconto > 0:
                desconto_aplicado = valor_original * (float(item.percentual_desconto) / 100.0)
            valor_total = max(0.0, valor_original - desconto_aplicado)

            nova_divida = Divida(
                id_local=item.id_local,
                cliente_id=cliente_uuid,
                usuario_id=usuario_uuid,
                valor_total=valor_total,
                valor_original=valor_original,
                desconto_aplicado=desconto_aplicado,
                percentual_desconto=float(item.percentual_desconto or 0.0),
                valor_pago=0.0,
                status="Pendente",
                observacao=item.observacao,
            )

            db.add(nova_divida)
            await db.flush()

            # Criar itens associados
            for it in item.itens:
                prod_uuid = _parse_uuid(it.produto_id)
                if not prod_uuid:
                    raise HTTPException(status_code=400, detail=f"produto_id inválido: {it.produto_id}")

                result_prod = await db.execute(select(Produto).where(Produto.id == prod_uuid))
                if not result_prod.scalar_one_or_none():
                    raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {it.produto_id}")

                db.add(
                    ItemDivida(
                        divida_id=nova_divida.id,
                        produto_id=prod_uuid,
                        quantidade=float(it.quantidade),
                        preco_unitario=float(it.preco_unitario),
                        subtotal=float(it.subtotal),
                    )
                )

            created += 1
        except HTTPException as he:
            # Erro específico deste registro; acumular mas continuar os demais
            errors.append({
                "index": idx,
                "id_local": item.id_local,
                "detail": he.detail,
            })
            await db.rollback()
        except Exception as ex:
            errors.append({
                "index": idx,
                "id_local": item.id_local,
                "detail": str(ex),
            })
            await db.rollback()

    # Commit uma vez ao final para as dívidas bem sucedidas
    try:
        await db.commit()
    except Exception as ex:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao finalizar sync de dívidas: {str(ex)}")

    return {
        "status": "ok" if not errors else "partial",
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


@router.get("/abertas", response_model=List[DividaOut])
async def listar_dividas_abertas(
    cliente_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Lista dívidas com status diferente de 'Quitado', opcionalmente filtrando por cliente."""
    try:
        # Join com Cliente para obter nome
        stmt = (
            select(Divida, Cliente.nome.label("cliente_nome"))
            .join(Cliente, Divida.cliente_id == Cliente.id, isouter=True)
            .where(Divida.status != "Quitado")
        )

        cliente_uuid = _parse_uuid(cliente_id)
        if cliente_uuid:
            stmt = stmt.where(Divida.cliente_id == cliente_uuid)

        result = await db.execute(stmt.order_by(Divida.data_divida.desc()))
        rows = result.all()

        resposta: list[DividaOut] = []
        for divida, cli_nome in rows:
            try:
                setattr(divida, 'cliente_nome', cli_nome)
            except Exception:
                setattr(divida, 'cliente_nome', None)
            resposta.append(DividaOut.model_validate(divida))
        return resposta
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar dívidas: {str(e)}")


@router.post("/{divida_id}/pagamentos", response_model=DividaOut)
async def registrar_pagamento_divida(divida_id: str, payload: PagamentoDividaIn, db: AsyncSession = Depends(get_db_session)):
    """Registra um pagamento (parcial ou total) para uma dívida existente."""
    if payload.valor <= 0:
        raise HTTPException(status_code=400, detail="Valor do pagamento deve ser maior que zero.")

    try:
        divida_uuid = _parse_uuid(divida_id)
        if not divida_uuid:
            raise HTTPException(status_code=400, detail="ID de dívida inválido.")

        result = await db.execute(select(Divida).where(Divida.id == divida_uuid))
        divida = result.scalar_one_or_none()
        if not divida:
            raise HTTPException(status_code=404, detail="Dívida não encontrada.")

        # Registrar pagamento
        usuario_uuid = _parse_uuid(payload.usuario_id)
        pagamento = PagamentoDivida(
            divida_id=divida.id,
            valor=float(payload.valor),
            forma_pagamento=payload.forma_pagamento,
            usuario_id=usuario_uuid,
        )
        db.add(pagamento)

        # Atualizar valores agregados da dívida
        novo_valor_pago = float(divida.valor_pago or 0.0) + float(payload.valor)
        divida.valor_pago = novo_valor_pago
        if novo_valor_pago >= float(divida.valor_total) - 0.01:
            divida.status = "Quitado"
        else:
            divida.status = "Parcial"

        # Persistir pagamento e atualização da dívida primeiro
        await db.commit()
        await db.refresh(divida)
        await db.refresh(pagamento)

        # Snapshot dos campos da dívida (evitar expirations após novo commit)
        snap_cli_nome = None
        snap_usr_nome = None
        try:
            if divida.cliente_id:
                r = await db.execute(select(Cliente.nome).where(Cliente.id == divida.cliente_id))
                snap_cli_nome = r.scalar_one_or_none()
        except Exception:
            snap_cli_nome = None
        try:
            if divida.usuario_id:
                r = await db.execute(select(User.nome).where(User.id == divida.usuario_id))
                snap_usr_nome = r.scalar_one_or_none()
        except Exception:
            snap_usr_nome = None

        divida_snapshot = {
            'id': divida.id,
            'id_local': divida.id_local,
            'cliente_id': divida.cliente_id,
            'usuario_id': divida.usuario_id,
            'cliente_nome': snap_cli_nome,
            'usuario_nome': snap_usr_nome,
            'data_divida': divida.data_divida,
            'valor_total': divida.valor_total,
            'valor_original': divida.valor_original,
            'desconto_aplicado': divida.desconto_aplicado,
            'percentual_desconto': divida.percentual_desconto,
            'valor_pago': divida.valor_pago,
            'status': divida.status,
            'observacao': divida.observacao,
        }

        # Criar uma Venda correspondente ao pagamento da dívida (contabilizar em relatórios)
        try:
            venda = Venda(
                usuario_id=usuario_uuid,
                cliente_id=divida.cliente_id,
                total=float(payload.valor),
                desconto=0.0,
                forma_pagamento=payload.forma_pagamento,
                observacoes=f"Pagamento de dívida #{divida.id_local if getattr(divida, 'id_local', None) is not None else divida_id}",
                cancelada=False,
            )
            db.add(venda)
            await db.flush()  # obter venda.id

            # Calcular custo proporcional do pagamento com base nos itens da dívida.
            # Sem isso, relatórios podem considerar custo=0 e lucro=valor_pago.
            custo_total_divida = 0.0
            try:
                itens_stmt = (
                    select(ItemDivida, Produto.preco_custo)
                    .join(Produto, ItemDivida.produto_id == Produto.id)
                    .where(ItemDivida.divida_id == divida.id)
                )
                itens_res = await db.execute(itens_stmt)
                itens_rows = itens_res.all() or []
                for it, preco_custo_prod in itens_rows:
                    try:
                        qtd = float(getattr(it, 'peso_kg', 0.0) or 0.0) if float(getattr(it, 'peso_kg', 0.0) or 0.0) > 0 else float(getattr(it, 'quantidade', 0.0) or 0.0)
                    except Exception:
                        qtd = float(getattr(it, 'quantidade', 0.0) or 0.0)
                    try:
                        custo_unit = float(preco_custo_prod or 0.0)
                    except Exception:
                        custo_unit = 0.0
                    custo_total_divida += custo_unit * qtd
            except Exception:
                custo_total_divida = 0.0

            valor_pago = float(payload.valor)
            try:
                total_divida = float(getattr(divida, 'valor_total', 0.0) or 0.0)
            except Exception:
                total_divida = 0.0
            fator = 1.0
            if total_divida > 1e-9:
                fator = max(0.0, min(1.0, valor_pago / total_divida))
            custo_pagamento = float(custo_total_divida) * float(fator)

            # Garantir produto marcador 'PAGDIV' (não afeta estoque)
            prod_stmt = select(Produto).where(Produto.codigo == "PAGDIV")
            prod_res = await db.execute(prod_stmt)
            prod = prod_res.scalar_one_or_none()
            if not prod:
                prod = Produto(
                    codigo="PAGDIV",
                    nome="Pagamento de Dívida",
                    descricao="Item sintético para registrar pagamento de dívida",
                    preco_custo=0.0,
                    preco_venda=0.0,
                    estoque=0.0,
                    estoque_minimo=0.0,
                    categoria_id=None,
                    venda_por_peso=False,
                    unidade_medida='un',
                    ativo=True,
                    taxa_iva=0.0,
                    codigo_imposto=None,
                )
                db.add(prod)
                await db.flush()

            # Criar item sintético proporcional ao valor pago
            item = ItemVenda(
                venda_id=venda.id,
                produto_id=prod.id,
                quantidade=1,
                peso_kg=0.0,
                preco_unitario=valor_pago,
                subtotal=valor_pago,
                preco_custo_unitario=custo_pagamento,
                taxa_iva=0.0,
                base_iva=0.0,
                valor_iva=0.0,
            )
            db.add(item)
            await db.commit()
        except Exception:
            # Não falhar o endpoint se a criação da venda falhar; apenas prosseguir
            await db.rollback()

        # Construir resposta a partir do snapshot para evitar acesso ao ORM após commit
        return _to_divida_out_from_snapshot(divida_snapshot)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao registrar pagamento da dívida: {str(e)}")
