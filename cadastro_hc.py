# cadastro_hc.py
# ---------------------------------------------------------------
# Requisitos (instale com):
#   pip install streamlit pandas python-dateutil pyodbc openpyxl
# Rode com: streamlit run cadastro_hc.py
# ---------------------------------------------------------------

import streamlit as st
import pandas as pd
import pyodbc
import os
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Tuple, Dict

# Importa utilitários de conexão SQL Server
from DB import get_conn, test_connection, get_config

# ------------------------------
# Config Básica
# ------------------------------
st.set_page_config(page_title="Presenças - Logística", layout="wide")

STATUS_OPCOES = ["", "PRESENTE", "BH", "ATRASADO", "FALTA", "FÉRIAS", 
                 "ATESTADO", "AFASTADO", "ANIVERSÁRIO", "SAIDA ANTC", 
                 "SIN ECOM", "SIN DIST", "SIN AVI", "SIN REC", "SIN EXP",
                 "SIN ALM", "SIN TEC", "DSR", "CURSO", "DESLIGADO"]

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
# Quem pode logar (email -> senha)
# --- LOGIN por e-mail (sem senha) --------------------------------------------
# Quem pode logar (lista de e-mails permitidos)
ALLOWED_EMAILS_DEFAULT = {
    "projetos.logistica@somagrupo.com.br",  # admin
    "lucas.silverio@somagrupo.com.br",      # usuário comum (sem admin)
}

# Quem é admin (pode complementar via st.secrets["admins"])
ADMIN_EMAILS = {
    "projetos.logistica@somagrupo.com.br",
}

def _allowed_emails():
    """
    Constrói o conjunto de e-mails autorizados:
    - os do código (ALLOWED_EMAILS_DEFAULT)
    - + os do st.secrets["users"], que podem ser dict (chaves = e-mails) ou list
    """
    emails = {e.lower() for e in ALLOWED_EMAILS_DEFAULT}
    try:
        secret_users = st.secrets.get("users", {})
        if isinstance(secret_users, dict):
            emails |= {k.lower() for k in secret_users.keys()}
        elif isinstance(secret_users, (list, set, tuple)):
            emails |= {str(e).lower() for e in secret_users}
    except Exception:
        pass
    return emails

def is_admin() -> bool:
    email = (st.session_state.get("user_email") or "").lower()
    try:
        admins_extra = set([e.lower() for e in st.secrets.get("admins", [])])
    except Exception:
        admins_extra = set()
    return email in (ADMIN_EMAILS | admins_extra)

# --- Helper: extrai nome do e-mail -------------------------------------------
def display_name_from_email(email: str) -> str:
    # pega a parte antes do @ e transforma em "Nome Sobrenome" (title case)
    local = (email or "").split("@")[0]
    if not local:
        return ""
    parts = local.replace("_", ".").replace("-", ".").split(".")
    parts = [p for p in parts if p]
    return " ".join(w.capitalize() for w in parts)

def show_login():
    st.markdown("<h2 style='text-align:center;'>Login</h2>", unsafe_allow_html=True)
    with st.form("login_somente_email"):
        email = st.text_input("E-mail").strip().lower()
        ok = st.form_submit_button("Entrar")

    if ok:
        allowed = _allowed_emails()
        if email in allowed:
            st.session_state["auth"] = True
            st.session_state["user_email"] = email
            st.success("Acesso liberado!")
            st.rerun()
        else:
            st.error("E-mail não autorizado.")

    st.stop()
# ----------------------------------------------------------------------------- 

# -------------------------------------------------------------------------------

# ------------------------------
# Banco (SQL Server) - Tabelas
# ------------------------------
def init_db():
    cn = get_conn()
    cur = cn.cursor()

    # leaders
    cur.execute("""
    IF OBJECT_ID('dbo.leaders', 'U') IS NULL
    CREATE TABLE dbo.leaders (
        id         INT IDENTITY(1,1) PRIMARY KEY,
        nome       NVARCHAR(200) NOT NULL,
        setor      NVARCHAR(100) NOT NULL,
        turno      NVARCHAR(20)  NOT NULL,
        created_at DATETIME2      DEFAULT SYSDATETIME()
    );
    """)

    # colaboradores
    cur.execute("""
    IF OBJECT_ID('dbo.colaboradores', 'U') IS NULL
    CREATE TABLE dbo.colaboradores (
        id         INT IDENTITY(1,1) PRIMARY KEY,
        nome       NVARCHAR(200) NOT NULL,
        setor      NVARCHAR(100) NOT NULL,
        turno      NVARCHAR(20)  NOT NULL,
        ativo      BIT           DEFAULT 1,
        created_at DATETIME2      DEFAULT SYSDATETIME()
    );
    """)

    # presencas (unique em colaborador_id+data)
    cur.execute("""
    IF OBJECT_ID('dbo.presencas', 'U') IS NULL
    CREATE TABLE dbo.presencas (
        id             INT IDENTITY(1,1) PRIMARY KEY,
        colaborador_id INT         NOT NULL,
        data           DATE        NOT NULL,
        status         NVARCHAR(20) NULL,
        setor          NVARCHAR(100) NOT NULL,
        turno          NVARCHAR(20)  NOT NULL,
        leader_nome    NVARCHAR(200) NULL,
        created_at     DATETIME2      DEFAULT SYSDATETIME(),
        updated_at     DATETIME2      NULL,
        CONSTRAINT UQ_presenca UNIQUE (colaborador_id, data),
        CONSTRAINT FK_presenca_colab FOREIGN KEY (colaborador_id) REFERENCES dbo.colaboradores(id)
    );
    """)

    cn.commit()
    cur.close()
    cn.close()

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

def data_minima_preenchimento(hoje: date | None = None) -> date:
    """Data mínima liberada = início do período (16..15) que contém 'hoje'."""
    h = hoje or date.today()
    inicio, _ = periodo_por_data(h)
    return inicio


# ------------------------------
# Camada de dados
# ------------------------------
def get_or_create_leader(nome: str, setor: str, turno: str) -> int:
    cn = get_conn(); cur = cn.cursor()
    cur.execute("SELECT id FROM dbo.leaders WHERE nome=? AND setor=? AND turno=?", (nome.strip(), setor, turno))
    row = cur.fetchone()
    if row:
        cur.close(); cn.close()
        return int(row[0])
    cur.execute("INSERT INTO dbo.leaders (nome, setor, turno) VALUES (?, ?, ?)", (nome.strip(), setor, turno))
    cn.commit()
    new_id = cur.execute("SELECT SCOPE_IDENTITY()").fetchone()[0]
    cur.close(); cn.close()
    return int(new_id)

def listar_colaboradores(setor: str, turno: str, somente_ativos=True) -> pd.DataFrame:
    cn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM dbo.colaboradores WHERE setor=? AND turno=?"
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql(query, cn, params=(setor, turno))
    cn.close()
    return df

def listar_colaboradores_por_setor(setor: str, somente_ativos=True) -> pd.DataFrame:
    cn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM dbo.colaboradores WHERE setor=?"
    params = [setor]
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql(query, cn, params=params)
    cn.close()
    return df

def listar_colaboradores_setor_turno(setor: str, turno: str, somente_ativos=True) -> pd.DataFrame:
    cn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM dbo.colaboradores WHERE setor=? AND turno=?"
    params = [setor, turno]
    if somente_ativos:
        query += " AND ativo=1"
    df = pd.read_sql(query, cn, params=params)
    cn.close()
    return df

def listar_todos_colaboradores(somente_ativos: bool = False) -> pd.DataFrame:
    cn = get_conn()
    query = "SELECT id, nome, setor, turno, ativo FROM dbo.colaboradores"
    if somente_ativos:
        query += " WHERE ativo=1"
    df = pd.read_sql(query, cn)
    cn.close()
    return df

def adicionar_colaborador(nome: str, setor: str, turno: str):
    turno = normaliza_turno(turno)
    cn = get_conn(); cur = cn.cursor()
    cur.execute(
        "INSERT INTO dbo.colaboradores (nome, setor, turno, ativo) VALUES (?, ?, ?, 1)",
        (nome.strip(), setor, turno),
    )
    cn.commit(); cur.close(); cn.close()

def atualizar_turno_colaborador(colab_id: int, novo_turno: str):
    novo_turno = normaliza_turno(novo_turno)
    cn = get_conn(); cur = cn.cursor()
    cur.execute("UPDATE dbo.colaboradores SET turno=? WHERE id=?", (novo_turno, colab_id))
    cn.commit(); cur.close(); cn.close()

def upsert_colaborador_turno(nome: str, setor: str, turno: str):
    turno = normaliza_turno(turno)
    cn = get_conn(); cur = cn.cursor()
    cur.execute("SELECT id FROM dbo.colaboradores WHERE nome=? AND setor=?", (nome.strip(), setor))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE dbo.colaboradores SET turno=?, ativo=1 WHERE id=?", (turno, int(row[0])))
    else:
        cur.execute(
            "INSERT INTO dbo.colaboradores (nome, setor, turno, ativo) VALUES (?, ?, ?, 1)",
            (nome.strip(), setor, turno),
        )
    cn.commit(); cur.close(); cn.close()

def atualizar_ativo_colaboradores(ids_para_inativar: List[int], ids_para_ativar: List[int]):
    cn = get_conn(); cur = cn.cursor()
    if ids_para_inativar:
        cur.execute(
            f"UPDATE dbo.colaboradores SET ativo=0 WHERE id IN ({','.join('?'*len(ids_para_inativar))})",
            ids_para_inativar,
        )
    if ids_para_ativar:
        cur.execute(
            f"UPDATE dbo.colaboradores SET ativo=1 WHERE id IN ({','.join('?'*len(ids_para_ativar))})",
            ids_para_ativar,
        )
    cn.commit(); cur.close(); cn.close()

def carregar_presencas(colab_ids: List[int], inicio: date, fim: date) -> Dict[Tuple[int, str], str]:
    if not colab_ids:
        return {}
    cn = get_conn(); cur = cn.cursor()
    placeholders = ",".join("?" * len(colab_ids))
    cur.execute(
        f"""
        SELECT colaborador_id,
               CONVERT(varchar(10), data, 23) AS data_iso,  -- yyyy-mm-dd
               status
        FROM dbo.presencas
        WHERE colaborador_id IN ({placeholders})
          AND data BETWEEN ? AND ?
        """,
        [*colab_ids, inicio, fim],
    )
    out = {(int(cid), d): (s or "") for cid, d, s in cur.fetchall()}
    cur.close(); cn.close()
    return out

def salvar_presencas(df_editado: pd.DataFrame, mapa_id_por_nome: Dict[str, int],
                     inicio: date, fim: date, setor: str, turno: str, leader_nome: str):
    # derrete apenas as colunas de DATA (ignora "Colaborador", "Setor" e "Turno")
    date_cols = [c for c in df_editado.columns if c not in ("Colaborador", "Setor", "Turno")]
    melt = df_editado.melt(
        id_vars=["Colaborador", "Setor"],          # pode manter sem "Turno"
        value_vars=date_cols,
        var_name="data",
        value_name="status"
    )
    melt["data_iso"] = pd.to_datetime(melt["data"]).dt.date
    melt["colaborador_id"] = melt["Colaborador"].map(mapa_id_por_nome)
    melt = melt.dropna(subset=["colaborador_id"])

    cn = get_conn(); cur = cn.cursor()
    for _, r in melt.iterrows():
        status = (r["status"] or "").strip()
        cid = int(r["colaborador_id"])
        dte = r["data_iso"]

        if status == "":
            cur.execute("DELETE FROM dbo.presencas WHERE colaborador_id=? AND data=?", (cid, dte))
        else:
            cur.execute("""
            MERGE dbo.presencas AS T
            USING (VALUES (?, ?)) AS S(colaborador_id, data)
                 ON T.colaborador_id = S.colaborador_id AND T.data = S.data
            WHEN MATCHED THEN
                UPDATE SET status=?, setor=?, turno=?, leader_nome=?, updated_at=SYSDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (colaborador_id, data, status, setor, turno, leader_nome, created_at, updated_at)
                VALUES (S.colaborador_id, S.data, ?, ?, ?, ?, SYSDATETIME(), SYSDATETIME());
            """, (cid, dte, status, setor, turno, leader_nome, status, setor, turno, leader_nome))
    cn.commit()
    cur.close(); cn.close()


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
            if col in ("Colaborador", "Setor", "Turno"):   # <— acrescentado "Turno"
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
        params = [dt_ini, dt_fim]
        if setor_sel != "Todos":
            params.append(setor_sel)
        if turno_sel != "Todos":
            params.append(turno_sel)

        df = pd.read_sql(
            f"""
            SELECT c.nome AS colaborador, p.data, p.status, p.setor, p.turno, p.leader_nome
              FROM dbo.presencas p JOIN dbo.colaboradores c ON c.id = p.colaborador_id
             WHERE p.data BETWEEN ? AND ?
             {"AND p.setor = ?" if setor_sel != "Todos" else ""}
             {"AND p.turno = ?" if turno_sel != "Todos" else ""}
             ORDER BY p.setor, p.turno, c.nome, p.data
            """,
            get_conn(),
            params=params,
        )

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
    "Aviamento": """GIL FERNANDO DANTAS PORTELA
PEDRO WILLIAM MARQUES ALVES
MAURO DA SILVA GUERRA
ALISSON RICHARD MOREIRA DOS SANTOS
JOSUE DE OLIVEIRA SOARES
FABRICIO SILVA MACHADO DA COSTA
THAIS LOPES LIMA
SIMONE AGUIAR SOUZA
MARIANA GOMES MARINHO
MARCIA CRISTINA REIS DO MONTE SILVA E SILVA
LILIANE CASTRO DE OLIVEIRA
JORGE LUIZ DA SILVA
MARCELO DA SILVA
GILMARA DE ASSIS
ELIANE PORTELLA LOPES
EDNA CEZAR NOGUEIRA DA SILVA
CARLOS EDUARDO LUIZ DE SOUZA
ANA ROSA PEREIRA COSTA
CAMILA SANTOS DE OLIVEIRA DA PAZ
AMANDA VIANNA ROSA
THAYNA NASCIMENTO DA SILVA
RODRIGO DE OLIVEIRA PESSOA
NATHALIA HELLEN VIDAL GOMES
SUELLEN TAVARES VIANA
AMANDA PEREIRA XAVIER
MARIA EDUARDA GRAVINO MUNIZ
MARCIO HERCULANO DE OLIVEIRA
REGINALDO GUEDES BEZERRA
LUIZ GUSTAVO ANCHIETA
JUAN LUCAS DA SILVA DE MORAES
FABIANO BARROS DE FREITAS
ROBERTA REIS DE ALMEIDA
THAIS MELLO DOS SANTOS
DOUGLAS OLIVEIRA DOS SANTOS
ANTHONY PAULO DA SILVA
ANDRE LUIS DE OLIVEIRA ARAUJO
SAIMER GONCALVES DA SILVA
CRISTIAN DA SILVA SALES
MARLY RODRIGUES DA SILVA ALEDI
CASSIA DE SOUZA SANTANA""",
    "Tecido": """FELIPE RAMOS TEIXEIRA
DOUGLAS DE ALBUQUERQUE MUNIZ
JEFFERSON GONCALVES DE SOUZA
HUDSON PAULO NASCIMENTO DE SOUZA
JOAO VICTOR CORDEIRO MOURA
LUCAS MATTOS SENNA PEREIRA
MARCIO FONTES
LUIZ FERNANDO SANTOS FURTADO
OSEIAS RIBEIRO DOS SANTOS
RAFAEL DE MELO SOBRINHO
JOAO VITOR DE ARRUDA GERONIMO
NATAN DUARTE RODRIGUES DA SILVA
RODRIGO ESTEVES BALBINO
EDUARDO MIGUEL DA SILVA
PATRICK DA SILVA MONSORES CASEMIRO
WESLEY VIANA DOS SANTOS
MAXWELL RONALD COSTA DA SILVA
EMERSON DE OLIVEIRA QUEIROZ DA SILVA
ROGERIO SANTA ROSA DE SANTANNA
JOSUE SANTOS DA PAZ
ISAC FRANK MELLO DOS SANTOS
ALEXSSANDRO REIS DE ANDRADE
GLEISON SOUZA SERRA
IRINEU ARAUJO DE SOUSA
LUCAS YAN NASCIMENTO DA SILVA""",
    "Distribuição": """ADRIANO DE OLIVEIRA GOMES
ALLAN ANSELMO SABINO FERNANDES
ANA CAROLINE DE MELO MOUTINHO
ANA CAROLINA NASCIMENTO BARBOSA
ANDERSON DA SILVA SOUZA
ANDRE DE OLIVEIRA ALBUQUERQUE
ANDRE GONCALVES DE OLIVEIRA MARTINS
AUGUSTO SANTOS DA SILVA
BEATRIZ CONCEIÇÃO DEODORO DA SILVA DA ROCHA
BRENO DOS SANTOS MOREIRA ROCHA
CLAUDETE PEREIRA
CRISTIANE DE MENEZES RODRIGUES
EDENILSON SOUZA DE MORAIS
EDSON VANDER DA SILVA LOPES
FABIANE CORSO VERMELHO
FABIO DA ROCHA SILVA JUNIOR
ISRAEL MIGUEL SOUZA
ISRAEL VANTINE FERNANDES
IVO DE LYRA JUNIOR
JOÃO MARCOS
JOÃO VICTOR
LEONARDO SANTANA DE ALMEIDA
LUIZ EDUARDO
LUCAS AZEVEDO
MATEUS DE MELLO
MATHEUS FERREIRA DE SOUZA
PEDRO PAULO DA SILVA
RODRIGO MOURA
Severina Lídia da Silva
WILLIAM SOUZA DA SILVA
WILSON MATEUS
WLADIMIR HORA
PEDRO HENRIQUE MENDES DOS SANTOS RIBEIRO
ADILSON DE ARAUJO SIQUEIRA
ANDERSON GARCEZ DOS SANTOS JUNIOR
BRENO GASPAR
DEIVISSON SILVA ALCANTARA
EVELIN
EZEQUIEL DA SILVA SOARES
HUDSON
IZABELA ROZA DUARTE DE SOUZA
JACQUELINE PAULINA FERREIRA
JUAN MICHEL DE OLIVEIRA SOUZA
LAERCIO BALDUINO ANDRADE
LUCAS DO NASCIMENTO FONTE
LUIZ CARLOS DURANS DO NASCIMENTO
MARCOS VINICIUS ANDRADE DOS SANTOS BRAGA
MATHEUS HENRIQUE FERREIRA
PATRICK MURILO OLIVEIRA DO NASCIMENTO
RAMON CORREA
RICARDO CORREIA DAS CHAGAS
RUANA PAIVA RANGEL
SAMUEL NOGUEIRA PERREIRA SOUZA MENDONÇA
SEBASTIÃO
TIAGO LEANDRO DAS CHAGAS
VALERIO
WILLIAN ALVAREGA
YURI AVILA
BRUNO DE SOUSA GAMA
DIEGO ASSUNÇÃO RODRIGUES DOS SANTOS
GABRIEL ALMEIDA DE LIMA SOUSA
GABRIEL CORREIA DA SILVA
GEIBERSON FELICIANO ARAGAO
GILSON ALVES DE SOUZA
IGOR FERREIRA  MUNIZ
JORGE THADEU DA SILVA BATISTA
LUAN BERNADO DO CARMO
LUCAS HENRIQUE DE ADRIANO GUILHERME
LUIS FERNANDO MONTEIRO DE MELO
MARCOS ALEXANDRE DA SILVA PRATES
MATEUS WILLIAN CASTRO BELISARIO DA SILVA
MATHEUS WASHINGTON FREIRES DOS SANTOS
NICOLLAS RIGAR VIRTUOSO
PEDRO JOSE DOS SANTOS MELO
VANDERLEY PEREIRA LEAL JUNIOR
WELLINGTON PEREIRA DA PAIXAO
WALLACE
HUDSON
LUCAS SILVA DO NASCIMENTO""",
    "Almoxarifado": """MARCIO LIMA DOS SANTOS
PATRICK DOS ANJOS LIMA
CARLA ALVES DOS SANTOS
WELLINGTON MATOS
LEANDRO FERNANDES DE OLIVEIRA
ANITOAN ALVES FEITOSA
RENNAN DA SILVA GOMES
GUILHERME DOS SANTOS FEITOSA
RAMON ROCHA DO CARMO""",
    "PAF": """DAVID DE ARAUJO MAIA
FELIPE SILVA DE FIGUEIREDO
JOELSON DOS SANTOS COUTINHO
RAPHAEL ABNER RODRIGUES MARREIROS
ROBSON SANTANA SILVA
SERGIO MURILO SIQUEIRA JUNIOR
WILLIAN LAUERMANN OLIVEIRA
CARLOS AUGUSTO LIMA MOURAO
ADRIANO MARINE WERNECK DE SOUSA
AGATA GURJAO FERREIRA
ANNA LUYSA SEVERINO NASCIMENTO
BRUNO DOS SANTOS BARRETO DO NASCIMENTO
MANOEL ARTUR SOUZA SANTOS
MOISES AUGUSTO DOS SANTOS DIAS
VICTOR HUGO MOTA CAMILLO
DIEGO FIGUEIREDO MARQUES
ALESSANDRO BOUCAS JORGE""",
    "Recebimento": """ANDREZA VALERIANO RAMOS PASSOS
BRAULIO CARDOSO DA SILVA
CHARLES DA SILVA COSTA
DENIS RODRIGUES DE SOUSA
EMERSON SANTOS
FABIO DA CONCEICAO FERREIRA
FLAVIO SANTOS DA SILVA
GABRIELLE DA SILVA PEREIRA
LUIZ EDUARDO CAMPOS DE SOUZA
MARCIA CRISTINA BARBOSA DE FREITAS
MARCOS VINICIUS SOUZA MARTINS
ROMULO DANIEL MARTINS PEREIRA
THAIS LIMA DE ANDRADE
THIAGO GOMES DE ARAUJO
UANDERSON FELIPE
WALLACY DE LIRA LEITE
ALLAN PIRES RODRIGUES
CLAUDIO DA SILVA
DANDARA MONTEIRO DA SILVA
HIGO JESSE PACHECO DE SOUZA
IAGO DE ALMEIDA ALVES PEREIRA
JEAN DE SA CARROCOSA
KAUÃ PABLO SIMIÃO DOS SANTOS
KAUANN SOUZA DE OLIVEIRA GOMES
LUCIANO SANTOS DE ARAUJO
LUIZ FILIPE SOUZA DE LIMA
MARLON DOUGLAS DE FREITAS
RAFAELA ANDRADE DA SILVA GUERRA
RENATO FERREIRA DOS SANTOS
RIGOALBERTO JOSUE VINOLES SALAZAR
SHEILA RIBEIRO DIAS TIBURCIO
THALIS DA SILVA FRANCO
YASMIM VIRGILIO DA SILVA
JULIO CESAR ALVES DE CARVALHO
LUIZ DOUGLAS PEREIRA
MARCOS ROBERTO
PATRICK COSTA DA SILVA BRAGA
VICTOR DA COSTA TEIXEIRA""",
    "Expedição": """ALEXSANDRO DOS REIS BASTOS
CARLOS JUNIOR FERREIRA SANTOS
DIEGO BORGES MARTINS
EMERSON ALVES PIRES
JOAO VITOR DE OLIVEIRA DE SOUZA
JONATHAN DOS SANTOS FEITOSA
LEANDRO COUTINHO
LEONARDO DOS SANTOS BARBOSA DA SILVA
LUIS CLAUDIO DIAS DA ROCHA
MARLON ALEXANDRE DE SOUSA
MATHEUS DOS SANTOS SILVA
MAYARA COUTINHO
PEDRO GUILHERME SANTOS QUELUCI
SAMUEL DA CONCEICAO SILVA
UDIRLEY OLIVEIRA SOARES
ANA CAROLINY DA SILVA
CLEISSON COSTA FERNANDES
DAVI DAS GRAÇAS MUNIZ BORGES
FELIPE MATOS DA ROCHA
GABRIEL LIMA TRAJANO DA SILVA
GABRIELLE SOZINHO LOUZA
JHONNATHA GABRIEL RIBEIRO DOS SANTOS LIMA
KAYKE ARAUJO MARQUES
LEONARDO DA SILVA GUIMARÃES
PEDRO HENRIQUE GONÇALVES DA ROCHA
RODRIGO SOUZA BRAGA
TAINARA CRISTINE DO NASCIMENTO
VINICIUS STEFANO DA SILVA BARBOSA
GUILHERME BORGES SANTOS""",
    "E-commerce": """ANA PAULA LIMA MOYSES
ARI RODRIGUES DO NASCIMENTO
CARLOS EDUARDO DE JESUS TEIXEIRA
DAIANA DA SILVA OLIVEIRA
EDILSON MATHEUS GONÇALVES DA SILVA
FELIPE DE SOUZA TOLEDO
JEFFERSON MATHEUS BITTENCOURT DA SILVA MACHADO
JONATHAN VIRGILIO DA SILVA
KAYKY WANDER ROSA SIMPLÍCIO
LEANDRO RODRIGUES DOS SANTOS
LEONARDO ROCHA SANTOS
LUCAS VICTOR DE SOUZA FERREIRA
LUIZA PEREIRA DOS SANTOS
LUZMARY DEL VALLE SALAZAR HERNANDEZ
NICHOLLAS RONNY COUTINHO FERREIRA
PEDRO JEMERSON ALVES DO NASCIMENTO
RAFAEL BRENDO SALES SANTANA
RAFAEL HENRIQUE MARCELINO ROMAO
RENATA DE LIMA ANDRADE
RODRIGO DOS SANTOS AZEVEDO
RONALDO INACIO DA SILVA
SHIRLEI MELLO DOS SANTOS
TATIANA GARCIA  CORREIA DO NASCIMENTO
WALLACE DE REZENDE SILVA
WESLEY DA SILVA BARCELOS
WILLIAM SILVA DE JESUS
ANA PAULA CUSTODIO DA SILVA GOMES DA SILVA
ANA PAULA LOPES DA CRUZ
ANDRÉA DA SILVA REIS
ANDREZA DE AZEVEDO NASCIMENTO DA SILVA
DANIELLE DA COSTA VIEIRA CAMARA
DAVI FRADIQUE DOS SANTOS SILVA
EDGARD DAS NEVES SILVA
EMILLY REIS GUILHERME FERREIRA
FABIANA MAGALHAES BRAGA
GUILHERME SILVA DE MELLO
ISABELA IARA SOUZA DA SILVA
JONAS SILVA DE SOUZA
JOYCE BOMFIM DE SANT ANNA
KAMILLE DOS SANTOS SOARES
KETLEN DOS REIS NASCIMENTO
LUAN CARVALHO SANTOS
LUCAS DE OLIVEIRA CASTRO
MARCELE SILVA DE OLIVEIRA
MARIANA PIRES VIEIRA
MATEUS DE SOUZA GOMES
MATHEUS PEREIRA CARNEIRO CESAR
RAYSSA SILVA DE OLIVEIRA CASTRO
RENAN PAIVA RANGEL
RICHARD RODRIGUES DE JESUS
VINICIUS DA SILVA OLIVEIRA
VITÓRIA SILVA ARAUJO
WENDEL PERIARD SILVA
WERICSON DA SILVA BARCELOS PAULA
YASMIN OLIVEIRA DE AVELLAR DA COSTA
ANA KAROLINA GOMES BRAZIL DE OLIVEIRA
ANDERSON SOARES DE SOUZA
ANTONIO CARLOS TORRACA
DALILA FERREIRA DA SILVA
DOUGLAS DE SOUZA LINS TOLEDO
GABRIEL MATEUS PATRICIO DA COSTA
JOSÉ RICARDO DA SILVA JUNIOR
MAYCON DOUGLAS DA COSTA SARMENTO
PABLO LUIZ PAES DE PAULA
PETER DOUGLAS FERREIRA DE SOUZA
RAFAELA CRISTINA DA SILVA MARQUES
RODRIGO SOARES BASTOS ROSALINO
RONALDO PINHEIRO ABREU
SUELEN CRISTINA DA SILVA BRAGA
THIAGO DA SILVA MOTA
VIVIANE MARTINS DE FREITAS
WALLACE ALVEZ COUTINHO
WELLINGTON MAURICIO
ZILTO PRATES JUNIOR
GIOVANNA DE CASTRO EMIDGIO
CHARLES RIBEIRO GONCALVES JUNIOR
TARCIANE GOMES DA CONCEIÇÃO
VITORIA ALVES BRAGA""",
}

def _parse_names(blob: str):
    return [n.strip().strip('"').strip("'") for n in blob.splitlines() if n.strip()]

def seed_colaboradores_iniciais(turno_default: str = "1°"):
    for setor, blob in SEED_LISTAS.items():
        for nome in _parse_names(blob):
            cn = get_conn(); cur = cn.cursor()
            cur.execute(
                "SELECT 1 FROM dbo.colaboradores WHERE nome=? AND setor=? AND turno=?",
                (nome, setor, turno_default),
            )
            exists = cur.fetchone()
            cur.close(); cn.close()
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

    # agora uso 5 colunas: Setor | Turno | Data | Filtro | Nome
    colA, colB, colC, colD, colE = st.columns([1, 1, 1, 1, 2])

    with colA:
        setor = st.selectbox("Setor", OPCOES_SETORES, index=0, key="lan_setor")

    with colB:
        turno_sel = st.selectbox("Turno", ["Todos"] + OPCOES_TURNOS, index=0, key="lan_turno")

    with colC:
        min_permitida = data_minima_preenchimento()
        data_dia = st.date_input(
        "Data do preenchimento",
        value=max(date.today(), min_permitida),  # garante valor inicial válido
        min_value=min_permitida,                 # <-- trava o passado antes do início do período
        format="DD/MM/YYYY",
        key="lan_data",
    )


    # >>> NOVO FILTRO AQUI <<<
    with colD:
        filtro_st = st.multiselect(
            "Filtro",
            options=["SOMA", "TERCEIROS"],
            default=["SOMA", "TERCEIROS"],  # ambos selecionados = mostra todos
            key="lan_filtro_st"
        )

    with colE:
        # preenche automaticamente com o nome derivado do e-mail logado
        default_name = display_name_from_email(st.session_state.get("user_email", ""))
        if default_name:
            st.text_input("Seu nome", value=default_name, key="lan_nome", disabled=True)
            nome_preenchedor = default_name
        else:
            nome_preenchedor = st.text_input("Seu nome (opcional)", key="lan_nome")

    # ------ busca colaboradores (Setor/Turno) ------
    if turno_sel == "Todos":
        df_cols = listar_colaboradores_por_setor(setor, somente_ativos=True)
    else:
        df_cols = listar_colaboradores_setor_turno(setor, turno_sel, somente_ativos=True)

    # ------ aplica o filtro SOMA / TERCEIROS ------
    # "TERCEIROS" = nome termina com "- terceiro" (case-insensitive, com ou sem espaços)
    mask_terceiro = df_cols["nome"].str.contains(r"-\s*terceiro\s*$", case=False, na=False)

    escolha = set(filtro_st)
    if escolha == {"SOMA"}:
        df_cols = df_cols[~mask_terceiro]
    elif escolha == {"TERCEIROS"}:
        df_cols = df_cols[mask_terceiro]
    else:
        # ambos selecionados (ou nenhum) -> não filtra
        pass

    if len(df_cols) == 0:
        st.warning("Nenhum colaborador cadastrado para este filtro.")
        st.stop()

    iso = data_dia.isoformat()
    base = pd.DataFrame(
        {
        "Colaborador": df_cols["nome"].tolist(),
        "Setor": df_cols["setor"].tolist(),
        "Turno": df_cols["turno"].tolist(),   # <— NOVO
        iso: ""
        },
        dtype="object"
    )


    pres = carregar_presencas(df_cols["id"].tolist(), data_dia, data_dia)
    mapa = dict(zip(df_cols["nome"], df_cols["id"]))
    base = aplicar_status_existentes(base, pres, mapa)
    

    cfg = {
    "Colaborador": st.column_config.TextColumn("Colaborador", disabled=True),
    "Setor": st.column_config.TextColumn("Setor", disabled=True),
    "Turno": st.column_config.TextColumn("Turno", disabled=True),   # <— NOVO
    iso: st.column_config.SelectboxColumn(
        label=data_dia.strftime("%d/%m"),
        options=STATUS_OPCOES,
        required=False,
    ),
}


    st.markdown("#### Tabela do dia")
    # incluir o filtro na chave do editor evita cache estranho ao alternar
    editor_key = f"editor_dia_{iso}_{setor}_{turno_sel}_{'-'.join(sorted(filtro_st) or ['TODOS'])}"
    editado = st.data_editor(
        base,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config=cfg,
        key=editor_key,
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
        st.success("Registros salvos/atualizados!")
        st.session_state.pop(editor_key, None)  
        st.rerun()

    with st.expander("Exportar CSV do dia", expanded=False):
        df = pd.read_sql(
            """
            SELECT c.nome AS colaborador, p.data, p.status, p.setor, p.turno, p.leader_nome
              FROM dbo.presencas p JOIN dbo.colaboradores c ON c.id = p.colaborador_id
             WHERE p.setor = ? AND p.data = ?
             ORDER BY colaborador
            """,
            get_conn(),
            params=(setor, iso),
        )
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
# Página de Configuração do DB
# ------------------------------
def pagina_db():
    st.markdown("### Configuração do Banco (SQL Server)")
    cfg = get_config()
    col1, col2 = st.columns(2)
    with col1:
        st.text_input("Servidor", value=cfg["SERVER"], disabled=True)
        st.text_input("Base de Dados", value=cfg["DATABASE"], disabled=True)
        st.text_input("Usuário", value=cfg["UID"], disabled=True)
    with col2:
        st.text_input("Encrypt", value=cfg["ENCRYPT"], disabled=True)
        st.text_input("TrustServerCertificate", value=cfg["TRUST_CERT"], disabled=True)
        st.text_input("Timeout (s)", value=str(cfg["CONNECT_TIMEOUT"]), disabled=True)

    st.caption("As credenciais são lidas de variáveis de ambiente. Altere-as no servidor/ambiente de execução.")

    if st.button("🔌 Testar conexão"):
        try:
            ok = test_connection()
            if ok:
                st.success("Conexão OK (SELECT 1 executado com sucesso).")
        except Exception as e:
            st.error(f"Falha ao conectar: {e}")

# ------------------------------
# Roteamento (com login)
# ------------------------------
if not st.session_state.get("auth", False):
    show_login()

# >>> Auto-import: rode uma vez por sessão (se quiser manter)
if not st.session_state.get("seed_loaded", False):
    # Se não quiser auto-import, comente as duas linhas abaixo
    # _try_auto_import_seed()
    st.session_state["seed_loaded"] = True

st.sidebar.title("Menu")
st.sidebar.caption(f"Usuário: {st.session_state.get('user_email','')}")
if st.sidebar.button("Sair"):
    for k in ("auth", "user_email"):
        st.session_state.pop(k, None)
    st.rerun()

# Opções de navegação (Colaboradores e DB só aparecem para admin)
nav_opts = ["Lançamento diário"] + (["Colaboradores"] if is_admin() else []) + ["Relatórios"] + (["DB"] if is_admin() else [])
escolha = st.sidebar.radio("Navegação", nav_opts, index=0)

# Painel Admin apenas para admin
if is_admin():
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

# Roteamento
if escolha == "Lançamento diário":
    pagina_lancamento_diario()
elif escolha == "Colaboradores":
    if not is_admin():
        st.error("Acesso restrito aos administradores.")
        st.stop()
    pagina_colaboradores()
elif escolha == "Relatórios":
    pagina_relatorios_globais()
elif escolha == "DB":
    if not is_admin():
        st.error("Acesso restrito aos administradores.")
        st.stop()
    pagina_db()

# Fim do arquivo
