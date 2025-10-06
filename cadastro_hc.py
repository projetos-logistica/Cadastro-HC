# app.py
# ---------------------------------------------------------------
# Requisitos (instale com):
#   pip install streamlit pandas python-dateutil
#   # (opcional) para exportar .xlsx: pip install openpyxl
# Rode com: streamlit run app.py
# ---------------------------------------------------------------

import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Dict

# ------------------------------
# Config Básica
# ------------------------------
st.set_page_config(page_title="Presenças - Logística", layout="wide")

STATUS_OPCOES = ["", "PRESENTE", "BH", "ATRASADO", "FALTA"]

OPCOES_SETORES = [
    "Aviamento",
    "Tecido",
    "Distribuição",
    "Almoxarifado",
    "PAF",
    "Recebimento",
    "Expedição",
    "E-commerce",
]
OPCOES_TURNOS = ["1°", "2°", "3°", "ÚNICO", "INTERMEDIARIO"]

def normaliza_turno(t: str) -> str:
    t = (t or "").strip().upper().replace("º", "°")
    if t == "UNICO":
        t = "ÚNICO"
    if t == "INTERMEDIÁRIO":
        t = "INTERMEDIARIO"
    return t if t in ["1°", "2°", "3°", "ÚNICO", "INTERMEDIARIO"] else "1°"

# --- LOGIN por e-mail/senha ---------------------------------------------------
DEFAULT_USERS = {
    "projetos.logistica@somagrupo.com.br": "projetos123",
}

def _valid_users():
    users = {k.lower(): v for k, v in DEFAULT_USERS.items()}
    try:
        secret_users = st.secrets.get("users", {})
        if isinstance(secret_users, dict):
            users.update({k.lower(): v for k, v in secret_users.items()})
    except Exception:
        pass
    return users

def show_login():
    st.markdown("<h2 style='text-align:center;'>Login</h2>", unsafe_allow_html=True)
    with st.form("login_email_senha"):
        email = st.text_input("E-mail").strip().lower()
        senha = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar")

    if ok:
        users = _valid_users()
        if email in users and senha == users[email]:
            st.session_state["auth"] = True
            st.session_state["user_email"] = email
            st.success("Acesso liberado!")
            st.rerun()
        else:
            st.error("E-mail ou senha inválidos.")

    st.stop()
# -------------------------------------------------------------------------------

# ------------------------------
# Banco (SQLite) - Tabelas normalizadas
# ------------------------------
def get_conn():
    return sqlite3.connect("presencas.db", check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leaders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            setor TEXT NOT NULL,
            turno TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS colaboradores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            setor TEXT NOT NULL,
            turno TEXT NOT NULL,
            ativo INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS presencas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colaborador_id INTEGER NOT NULL,
            data TEXT NOT NULL,                -- ISO yyyy-mm-dd
            status TEXT,                       -- uma das STATUS_OPCOES
            setor TEXT NOT NULL,
            turno TEXT NOT NULL,
            leader_nome TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT,
            UNIQUE(colaborador_id, data),
            FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id)
        )
        """
    )

    conn.commit()
    conn.close()

init_db()

def _try_auto_import_seed():
    caminhos = [
        "Turno Colaboradores.xlsx",
        "turnos.xlsx",
        "turnos.csv",
        "/mnt/data/Turno Colaboradores.xlsx",
    ]
    for p in caminhos:
        try:
            if os.path.exists(p):
                with open(p, "rb") as f:
                    importar_turnos_de_arquivo(f, setor_padrao=None)
                break
        except Exception:
            pass

_try_auto_import_seed()

# ------------------------------
# Utilitários de período (16..15)
# ------------------------------
MESES_PT = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]

def periodo_por_data(ref: date) -> Tuple[date, date]:
    if ref.day >= 16:
        inicio = ref.replace(day=16)
    else:
        inicio = (ref.replace(day=1) - relativedelta(months=1)).replace(day=16)
    fim = (inicio + relativedelta(months=1)).replace(day=15)
    return inicio, fim

def listar_periodos(n: int = 12) -> List[Tuple[str, date, date]]:
    hoje = date.today()
    inicio_atual, _ = periodo_por_data(hoje)
    periodos = []
    for i in range(n):
        ini = inicio_atual - relativedelta(months=i)
        fim = (ini + relativedelta(months=1)).replace(day=15)
        rotulo = f"{ini.day} {MESES_PT[ini.month-1]} {ini.year} – {fim.day} {MESES_PT[fim.month-1]} {fim.year}"
        periodos.append((rotulo, ini, fim))
    return periodos

def datas_do_periodo(inicio: date, fim: date) -> List[date]:
    n = (fim - inicio).days + 1
    return [inicio + timedelta(days=i) for i in range(n)]

# ------------------------------
# Camada de dados
# ------------------------------
def get_or_create_leader(nome: str, setor: str, turno: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM leaders WHERE nome=? AND setor=? AND turno=?",
        (nome.strip(), setor, turno),
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return row[0]
    cur.execute(
        "INSERT INTO leaders (nome, setor, turno) VALUES (?, ?, ?)",
        (nome.strip(), setor, turno),
    )
    conn.commit()
    leader_id = cur.lastrowid
    conn.close()
    return leader_id

def listar_colaboradores(setor: str, turno: str, somente_ativos=True) -> pd.DataFrame:
    conn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM colaboradores WHERE setor=? AND turno=?"
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql_query(query, conn, params=(setor, turno))
    conn.close()
    return df

def listar_colaboradores_por_setor(setor: str, somente_ativos=True) -> pd.DataFrame:
    conn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM colaboradores WHERE setor=?"
    params = [setor]
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

def listar_colaboradores_setor_turno(setor: str, turno: str, somente_ativos=True) -> pd.DataFrame:
    conn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM colaboradores WHERE setor=? AND turno=?"
    params = [setor, turno]
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

def listar_todos_colaboradores(somente_ativos: bool = False) -> pd.DataFrame:
    conn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM colaboradores"
    if somente_ativos:
        query += " WHERE ativo=1"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def adicionar_colaborador(nome: str, setor: str, turno: str):
    turno = normaliza_turno(turno)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO colaboradores (nome, setor, turno, ativo) VALUES (?, ?, ?, 1)",
        (nome.strip(), setor, turno),
    )
    conn.commit()
    conn.close()

def atualizar_turno_colaborador(colab_id: int, novo_turno: str):
    novo_turno = normaliza_turno(novo_turno)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE colaboradores SET turno=? WHERE id=?", (novo_turno, colab_id))
    conn.commit()
    conn.close()

def upsert_colaborador_turno(nome: str, setor: str, turno: str):
    turno = normaliza_turno(turno)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM colaboradores WHERE nome=? AND setor=?", (nome.strip(), setor))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE colaboradores SET turno=?, ativo=1 WHERE id=?", (turno, row[0]))
    else:
        cur.execute(
            "INSERT INTO colaboradores (nome, setor, turno, ativo) VALUES (?, ?, ?, 1)",
            (nome.strip(), setor, turno),
        )
    conn.commit()
    conn.close()

def atualizar_ativo_colaboradores(ids_para_inativar: List[int], ids_para_ativar: List[int]):
    conn = get_conn()
    cur = conn.cursor()
    if ids_para_inativar:
        cur.execute(
            f"UPDATE colaboradores SET ativo=0 WHERE id IN ({','.join('?'*len(ids_para_inativar))})",
            ids_para_inativar,
        )
    if ids_para_ativar:
        cur.execute(
            f"UPDATE colaboradores SET ativo=1 WHERE id IN ({','.join('?'*len(ids_para_ativar))})",
            ids_para_ativar,
        )
    conn.commit()
    conn.close()

def carregar_presencas(colab_ids: List[int], inicio: date, fim: date) -> Dict[Tuple[int, str], str]:
    if not colab_ids:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT colaborador_id, data, status
          FROM presencas
         WHERE colaborador_id IN ({','.join('?'*len(colab_ids))})
           AND date(data) BETWEEN date(?) AND date(?)
        """,
        [*colab_ids, inicio.isoformat(), fim.isoformat()],
    )
    out = {(cid, d): s or "" for cid, d, s in cur.fetchall()}
    conn.close()
    return out

def salvar_presencas(df_editado: pd.DataFrame, mapa_id_por_nome: Dict[str, int],
                     inicio: date, fim: date, setor: str, turno: str, leader_nome: str):
    # derrete apenas as colunas de DATA (ignora "Colaborador" e "Setor")
    date_cols = [c for c in df_editado.columns if c not in ("Colaborador", "Setor")]
    melt = df_editado.melt(id_vars=["Colaborador", "Setor"],
                           value_vars=date_cols,
                           var_name="data",
                           value_name="status")
    melt["data_iso"] = pd.to_datetime(melt["data"]).dt.date.astype(str)
    melt["colaborador_id"] = melt["Colaborador"].map(mapa_id_por_nome)
    melt = melt.dropna(subset=["colaborador_id"])

    conn = get_conn()
    cur = conn.cursor()

    for _, r in melt.iterrows():
        status = (r["status"] or "").strip()
        if status == "":
            cur.execute(
                "DELETE FROM presencas WHERE colaborador_id=? AND data=?",
                (int(r["colaborador_id"]), r["data_iso"]),
            )
        else:
            cur.execute(
                """
                INSERT INTO presencas (colaborador_id, data, status, setor, turno, leader_nome, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(colaborador_id, data) DO UPDATE SET
                    status=excluded.status,
                    setor=excluded.setor,
                    turno=excluded.turno,
                    leader_nome=excluded.leader_nome,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    int(r["colaborador_id"]),
                    r["data_iso"],
                    status,
                    setor,
                    turno,
                    leader_nome,
                ),
            )

    conn.commit()
    conn.close()

# ------------------------------
# UI Helpers
# ------------------------------
def montar_grid_presencas(df_cols: pd.DataFrame, inicio: date, fim: date) -> pd.DataFrame:
    dias = datas_do_periodo(inicio, fim)
    base = pd.DataFrame({"Colaborador": df_cols["nome"].tolist(), "Setor": df_cols["setor"].tolist()})
    for d in dias:
        base[d.isoformat()] = ""
    return base

def aplicar_status_existentes(base: pd.DataFrame,
                              presencas: Dict[Tuple[int, str], str],
                              mapa_id_por_nome: Dict[str, int]):
    for nome, cid in mapa_id_por_nome.items():
        for col in base.columns:
            if col in ("Colaborador", "Setor"):
                continue
            key = (cid, col)
            if key in presencas:
                base.loc[base["Colaborador"] == nome, col] = presencas[key]
    return base

def coluna_config_datas(inicio: date, fim: date) -> Dict[str, st.column_config.Column]:
    cfg = {}
    dias = datas_do_periodo(inicio, fim)
    for d in dias:
        label = d.strftime("%d/%m")
        cfg[d.isoformat()] = st.column_config.SelectboxColumn(
            label=label,
            help="Selecione o status para este dia",
            options=STATUS_OPCOES,
            required=False,
        )
    return cfg

# ------------------------------
# Páginas
# ------------------------------
def pagina_colaboradores():
    st.markdown("### Colaboradores por Setor/Turno")
    colf1, colf2 = st.columns([1, 1])
    with colf1:
        setor = st.selectbox("Setor", OPCOES_SETORES, index=0, key="cols_setor")
    with colf2:
        turno_filtro = st.selectbox("Turno", ["Todos"] + OPCOES_TURNOS, index=0, key="cols_turno")

    # Busca conforme filtro
    if turno_filtro == "Todos":
        df_all = listar_colaboradores_por_setor(setor, somente_ativos=False)
    else:
        df_all = listar_colaboradores_setor_turno(setor, turno_filtro, somente_ativos=False)

    df_ativos = df_all[df_all["ativo"] == 1]
    df_inativos = df_all[df_all["ativo"] == 0]

    # --- Adicionar novo colaborador
    with st.expander("Adicionar novo colaborador", expanded=False):
        with st.form("add_colab"):
            nome = st.text_input("Nome do colaborador")
            turno_new = st.selectbox("Turno", OPCOES_TURNOS, index=0)
            ok = st.form_submit_button("Adicionar")
        if ok:
            if nome.strip():
                adicionar_colaborador(nome, setor, turno_new)
                st.success(f"Colaborador '{nome}' adicionado ao setor {setor} com turno {turno_new}!")
                st.rerun()
            else:
                st.warning("Informe um nome válido.")

    # --- Excluir colaborador (remove da lista de ativos)
    with st.expander("Excluir colaborador (remover da lista)", expanded=False):
        st.caption("A exclusão aqui **inativa** o colaborador (não apaga o histórico).")
        if df_ativos.empty:
            st.info("Não há colaboradores ativos nesse filtro.")
        else:
            # Lista somente os ATIVOS para excluir
            opcoes_del = {
                f"{row['nome']} (ID {row['id']})": int(row['id'])
                for _, row in df_ativos.sort_values('nome').iterrows()
            }
            escolha_del = st.selectbox("Selecione o colaborador para excluir", list(opcoes_del.keys()))
            if st.button("Excluir colaborador", type="primary", key="btn_del_colab"):
                # Inativa o colaborador selecionado
                atualizar_ativo_colaboradores([opcoes_del[escolha_del]], [])
                st.success("Colaborador removido da lista de ativos (inativado).")
                st.rerun()

    # --- Editar turno (mantido como estava)
    with st.expander("Editar turno de colaborador", expanded=False):
        if df_all.empty:
            st.info("Nenhum colaborador listado no filtro atual.")
        else:
            opcoes = {f"{row['nome']} (ID {row['id']})": int(row['id']) for _, row in df_all.sort_values('nome').iterrows()}
            escolha = st.selectbox("Selecione o colaborador", list(opcoes.keys()))
            novo_turno = st.selectbox("Novo turno", OPCOES_TURNOS, index=0)
            if st.button("Atualizar turno"):
                atualizar_turno_colaborador(opcoes[escolha], novo_turno)
                st.success("Turno atualizado!")
                st.rerun()

    # --- Tabelas
    colA, colB = st.columns(2)
    with colA:
        st.subheader("Ativos")
        if len(df_ativos) == 0:
            st.info("Nenhum colaborador ativo para este filtro.")
        else:
            st.dataframe(
                df_ativos[["id", "nome", "turno"]]
                .rename(columns={"id": "ID", "nome": "Nome", "turno": "Turno"}),
                use_container_width=True
            )
    with colB:
        st.subheader("Inativos")
        st.dataframe(
            df_inativos[["id", "nome", "turno"]]
            .rename(columns={"id": "ID", "nome": "Nome", "turno": "Turno"}),
            use_container_width=True
        )


def pagina_preenchimento():
    return pagina_lancamento_diario()

def pagina_relatorios_globais():
    st.markdown("### Relatórios Globais (todos os setores/turnos)")
    col1, col2 = st.columns(2)
    with col1:
        dt_ini = st.date_input("Data inicial", value=periodo_por_data(date.today())[0])
    with col2:
        dt_fim = st.date_input("Data final", value=periodo_por_data(date.today())[1])

    col3, col4 = st.columns(2)
    with col3:
        setor_sel = st.selectbox("Filtrar por Setor", ["Todos"] + OPCOES_SETORES, index=0)
    with col4:
        turno_sel = st.selectbox("Filtrar por Turno", ["Todos"] + OPCOES_TURNOS, index=0)

    if st.button("Gerar relatório"):
        where = ["date(p.data) BETWEEN date(?) AND date(?)"]
        params = [dt_ini.isoformat(), dt_fim.isoformat()]
        if setor_sel != "Todos":
            where.append("p.setor = ?")
            params.append(setor_sel)
        if turno_sel != "Todos":
            where.append("p.turno = ?")
            params.append(turno_sel)

        conn = get_conn()
        df = pd.read_sql_query(
            f"""
            SELECT c.nome AS colaborador, p.data, p.status, p.setor, p.turno, p.leader_nome
              FROM presencas p JOIN colaboradores c ON c.id = p.colaborador_id
             WHERE {' AND '.join(where)}
             ORDER BY p.setor, p.turno, c.nome, p.data
            """,
            conn,
            params=params,
        )
        conn.close()

        if df.empty:
            st.info("Sem dados no intervalo/filtros informados.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            tag_setor = setor_sel if setor_sel != "Todos" else "todos_setores"
            tag_turno = turno_sel if turno_sel != "Todos" else "todos_turnos"
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Baixar CSV",
                data=csv,
                file_name=f"presencas_{tag_setor}_{tag_turno}_{dt_ini}_{dt_fim}.csv",
                mime="text/csv",
            )

# ------------------------------
# Seed de colaboradores (opcional / one-off)
# ------------------------------
SEED_LISTAS = {
    "Almoxarifado": """MARCIO LIMA DOS SANTOS
PATRICK DOS ANJOS LIMA
CARLA ALVES DOS SANTOS
WELLINGTON MATOS
LEANDRO FERNANDES DE OLIVEIRA
ANITOAN ALVES FEITOSA
RENNAN DA SILVA GOMES
GUILHERME DOS SANTOS FEITOSA
RAMON ROCHA DO CARMO""",
    # ... (demais setores iguais ao seu arquivo original)
    # Para manter a resposta mais curta, omiti aqui os outros blocos de nomes.
    # Cole os mesmos SEED_LISTAS completos que você já tem.
}

def _parse_names(blob: str):
    return [n.strip().strip('"').strip("'") for n in blob.splitlines() if n.strip()]

def seed_colaboradores_iniciais(turno_default: str = "1°"):
    for setor, blob in SEED_LISTAS.items():
        for nome in _parse_names(blob):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM colaboradores WHERE nome=? AND setor=? AND turno=?",
                (nome, setor, turno_default),
            )
            exists = cur.fetchone()
            conn.close()
            if not exists:
                adicionar_colaborador(nome, setor, turno_default)

# ------------------------------
# Importador de turnos (xlsx/csv)
# ------------------------------
def _normalize_setor(nome_sheet: str) -> str:
    s = (nome_sheet or "").strip().upper()
    mapa = {
        "AVIAMENTO": "Aviamento",
        "TECIDO": "Tecido",
        "DISTRIBUICAO": "Distribuição",
        "DISTRIBUIÇÃO": "Distribuição",
        "ALMOXARIFADO": "Almoxarifado",
        "PAF": "PAF",
        "RECEBIMENTO": "Recebimento",
        "EXPEDICAO": "Expedição",
        "EXPEDIÇÃO": "Expedição",
        "E-COMMERCE": "E-commerce",
        "ECOMMERCE": "E-commerce",
        "E COMMERCE": "E-commerce",
    }
    return mapa.get(s, nome_sheet)

def importar_turnos_de_arquivo(arquivo, setor_padrao: str | None = None) -> int:
    nome = getattr(arquivo, "name", "").lower()
    total = 0

    def _process_df(df: pd.DataFrame, setor_hint: str | None = None):
        nonlocal total
        cols = {c.upper(): c for c in df.columns}
        nome_col = cols.get("NOME COMPLETO") or cols.get("NOME")
        turno_col = cols.get("TURNO")
        setor_col = cols.get("SETOR")
        if not nome_col or not turno_col:
            return 0
        linhas = 0
        for _, row in df.iterrows():
            nome_val = str(row[nome_col]).strip()
            if not nome_val:
                continue
            turno_val = normaliza_turno(str(row[turno_col]).strip())
            setor_val = _normalize_setor(str(row[setor_col]).strip()) if setor_col else setor_hint or setor_padrao
            if not setor_val:
                raise ValueError("Defina o setor (coluna SETOR no arquivo ou selecione na UI para CSV sem SETOR).")
            upsert_colaborador_turno(nome_val, setor_val, turno_val)
            linhas += 1
        total += linhas
        return linhas

    if nome.endswith((".xlsx", ".xls")):
        xls = pd.ExcelFile(arquivo)
        for aba in xls.sheet_names:
            df = xls.parse(aba)
            _process_df(df, setor_hint=_normalize_setor(aba))
    else:
        try:
            df = pd.read_csv(arquivo)
        except Exception:
            df = pd.read_csv(arquivo, encoding="latin1", sep=None, engine="python")
        _process_df(df, setor_hint=None)

    return total

# ------------------------------
# Página de Lançamento Diário
# ------------------------------
def pagina_lancamento_diario():
    st.markdown("### Lançamento diário de presença (por setor)")
    colA, colB, colC, colD = st.columns([1,1,1,1])
    with colA:
        setor = st.selectbox("Setor", OPCOES_SETORES, index=0)
    with colB:
        turno_sel = st.selectbox("Turno", ["Todos"] + OPCOES_TURNOS, index=0)
    with colC:
        data_dia = st.date_input("Data do preenchimento", value=date.today())
    with colD:
        nome_preenchedor = st.text_input("Seu nome (opcional)")

    if turno_sel == "Todos":
        df_cols = listar_colaboradores_por_setor(setor, somente_ativos=True)
    else:
        df_cols = listar_colaboradores_setor_turno(setor, turno_sel, somente_ativos=True)

    if len(df_cols) == 0:
        st.warning("Nenhum colaborador cadastrado para este filtro.")
        st.stop()

    iso = data_dia.isoformat()
    base = pd.DataFrame(
        {"Colaborador": df_cols["nome"].tolist(), "Setor": df_cols["setor"].tolist(), iso: ""},
        dtype="object"
    )

    pres = carregar_presencas(df_cols["id"].tolist(), data_dia, data_dia)
    mapa = dict(zip(df_cols["nome"], df_cols["id"]))
    base = aplicar_status_existentes(base, pres, mapa)

    cfg = {
        "Colaborador": st.column_config.TextColumn("Colaborador", disabled=True),
        "Setor": st.column_config.TextColumn("Setor", disabled=True),
        iso: st.column_config.SelectboxColumn(
            label=data_dia.strftime("%d/%m"),
            options=STATUS_OPCOES,
            required=False,
        ),
    }

    st.markdown("#### Tabela do dia")
    editado = st.data_editor(
        base,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config=cfg,
        key=f"editor_dia_{iso}_{setor}_{turno_sel}",
    )

    if st.button("Salvar dia"):
        salvar_presencas(
            editado,
            mapa,
            data_dia,
            data_dia,
            setor,
            turno=(turno_sel if turno_sel != "Todos" else "-"),
            leader_nome=nome_preenchedor or "",
        )
        st.success("Registros salvos!")

    with st.expander("Exportar CSV do dia", expanded=False):
        conn = get_conn()
        df = pd.read_sql_query(
            """
            SELECT c.nome AS colaborador, p.data, p.status, p.setor, p.turno, p.leader_nome
              FROM presencas p JOIN colaboradores c ON c.id = p.colaborador_id
             WHERE p.setor=? AND date(p.data)=date(?)
             ORDER BY colaborador
            """,
            conn,
            params=(setor, iso),
        )
        conn.close()
        if df.empty:
            st.info("Sem dados salvos para esse dia.")
        else:
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Baixar CSV",
                data=csv,
                file_name=f"presencas_{setor}_{iso}.csv",
                mime="text/csv",
            )

# ------------------------------
# Roteamento (com login)
# ------------------------------
if not st.session_state.get("auth", False):
    show_login()

st.sidebar.title("Menu")
st.sidebar.caption(f"Usuário: {st.session_state.get('user_email','')}")
if st.sidebar.button("Sair"):
    for k in ("auth", "user_email"):
        st.session_state.pop(k, None)
    st.rerun()

# >>> Removido "Turnos" do menu
escolha = st.sidebar.radio("Navegação", ["Lançamento diário", "Colaboradores", "Relatórios"], index=0)

with st.sidebar.expander("⚙️ Admin"):
    coladm1, coladm2 = st.columns([1,1])
    if coladm1.button("Carregar lista inicial de colaboradores"):
        seed_colaboradores_iniciais(turno_default="1°")
        st.success("Seed aplicado (somente adiciona quem não existe).")

    up = st.file_uploader("Importar turnos (xlsx/csv)", type=["xlsx", "xls", "csv"], key="up_turnos")
    setor_default = st.selectbox("Se o CSV não tiver coluna SETOR, aplicar a:",
                                 ["(obrigatório se CSV sem SETOR)"] + OPCOES_SETORES, index=0)
    if st.button("Aplicar turnos do arquivo"):
        if up is None:
            st.warning("Selecione um arquivo .xlsx ou .csv")
        else:
            try:
                n = importar_turnos_de_arquivo(up, setor_padrao=None if setor_default.startswith("(") else setor_default)
                st.success(f"Turnos aplicados/atualizados para {n} colaboradores.")
            except Exception as e:
                st.error(f"Erro ao importar: {e}")

if escolha == "Lançamento diário":
    pagina_lancamento_diario()
elif escolha == "Colaboradores":
    pagina_colaboradores()
else:
    pagina_relatorios_globais()

# Fim do arquivo
