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

STATUS_OPCOES = [
    "",
    "PRESENTE",
    "BH",
    "ATRASADO",
    "FALTA",
]

# Você pode alterar/ordenar estas listas conforme a realidade
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

# Normalização de turno (aceita 1º/1°, UNICO/ÚNICO, INTERMEDIÁRIO/INTERMEDIARIO)
def normaliza_turno(t: str) -> str:
    t = (t or "").strip().upper().replace("º", "°")
    if t == "UNICO":
        t = "ÚNICO"
    if t == "INTERMEDIÁRIO":
        t = "INTERMEDIARIO"
    return t if t in ["1°", "2°", "3°", "ÚNICO", "INTERMEDIARIO"] else "1°"

# ------------------------------
# Banco (SQLite) - Tabelas normalizadas
# ------------------------------

def get_conn():
    # check_same_thread=False permite uso no Streamlit
    conn = sqlite3.connect("presencas.db", check_same_thread=False)
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Líder que entra pela tela inicial
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

    # Colaboradores (dimensão). "ativo" permite desativar sem apagar histórico
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

    # Fato de presença (granular por COLABORADOR + DATA)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS presencas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colaborador_id INTEGER NOT NULL,
            data TEXT NOT NULL,                -- ISO yyyy-mm-dd
            status TEXT,                       -- uma das STATUS_OPCOES
            setor TEXT NOT NULL,
            turno TEXT NOT NULL,
            leader_nome TEXT,                  -- quem preencheu
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

# Tenta importar turnos automaticamente de um arquivo local (opcional)
# Coloque ao lado do app: "Turno Colaboradores.xlsx" ou "turnos.csv"
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
            # silencioso: se falhar aqui, o usuário ainda pode usar o uploader no Admin
            pass

_try_auto_import_seed()

# ------------------------------
# Utilitários de período (16..15)
# ------------------------------

MESES_PT = [
    "jan", "fev", "mar", "abr", "mai", "jun",
    "jul", "ago", "set", "out", "nov", "dez"
]


def periodo_por_data(ref: date) -> Tuple[date, date]:
    """Retorna (inicio, fim) do período que contém a data ref.
    Período sempre de 16/M até 15/(M+1).
    """
    if ref.day >= 16:
        inicio = ref.replace(day=16)
    else:
        inicio = (ref.replace(day=1) - relativedelta(months=1)).replace(day=16)
    fim = (inicio + relativedelta(months=1)).replace(day=15)
    return inicio, fim


def listar_periodos(n: int = 12) -> List[Tuple[str, date, date]]:
    """Lista N períodos retroativos (inclui o atual)."""
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

# Nova: lista somente por setor (ignora turno)

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


def salvar_presencas(df_editado: pd.DataFrame, mapa_id_por_nome: Dict[str, int], inicio: date, fim: date, setor: str, turno: str, leader_nome: str):
    # Converte matriz (colunas = datas) em registros normalizados
    melt = df_editado.melt(id_vars=["Colaborador"], var_name="data", value_name="status")
    melt["data_iso"] = pd.to_datetime(melt["data"]).dt.date.astype(str)
    melt["colaborador_id"] = melt["Colaborador"].map(mapa_id_por_nome)
    melt = melt.dropna(subset=["colaborador_id"])  # segurança

    conn = get_conn()
    cur = conn.cursor()

    for _, r in melt.iterrows():
        status = (r["status"] or "").strip()
        if status == "":
            # Se vazio, removemos o registro (deixa como não preenchido)
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
    # Monta DF base com colunas de datas ISO (mantém normalizado) mas exibe label dd/mm
    base = pd.DataFrame({"Colaborador": df_cols["nome"].tolist()})
    for d in dias:
        base[d.isoformat()] = ""
    return base


def aplicar_status_existentes(base: pd.DataFrame, presencas: Dict[Tuple[int, str], str], mapa_id_por_nome: Dict[str, int]):
    for nome, cid in mapa_id_por_nome.items():
        for col in base.columns:
            if col == "Colaborador":
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

def pagina_login():
    st.markdown("## Registro do Líder")
    with st.form("login_form"):
        col1, col2 = st.columns(2)
        with col1:
            setor = st.selectbox("Setor", OPCOES_SETORES, index=0, key="login_setor")
            turno = st.selectbox("Turno", OPCOES_TURNOS, index=0, key="login_turno")
        with col2:
            nome = st.text_input("Seu nome (líder)", key="login_nome")
            st.caption("Esta identificação ficará gravada junto ao preenchimento.")

        submitted = st.form_submit_button("Entrar")

    if submitted:
        if not nome.strip():
            st.error("Informe seu nome.")
            return
        leader_id = get_or_create_leader(nome, setor, turno)
        st.session_state["leader_id"] = leader_id
        st.session_state["leader_nome"] = nome.strip()
        st.session_state["setor"] = setor
        st.session_state["turno"] = turno
        st.session_state["logado"] = True
        st.success("Bem-vindo! Carregando sua tela de preenchimento…")
        st.rerun()


def pagina_colaboradores():
    st.markdown("### Colaboradores por Setor/Turno")
    colf1, colf2 = st.columns([1, 1])
    with colf1:
        setor = st.selectbox("Setor", OPCOES_SETORES, index=0, key="cols_setor")
    with colf2:
        turno_filtro = st.selectbox("Turno", ["Todos"] + OPCOES_TURNOS, index=0, key="cols_turno")

    if turno_filtro == "Todos":
        df_all = listar_colaboradores_por_setor(setor, somente_ativos=False)
    else:
        df_all = listar_colaboradores_setor_turno(setor, turno_filtro, somente_ativos=False)

    df_ativos = df_all[df_all["ativo"] == 1]
    df_inativos = df_all[df_all["ativo"] == 0]

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

    colA, colB = st.columns(2)
    with colA:
        st.subheader("Ativos")
        if len(df_ativos) == 0:
            st.info("Nenhum colaborador ativo para este filtro.")
        else:
            st.dataframe(df_ativos[["id", "nome", "turno"]].rename(columns={"id": "ID", "nome": "Nome", "turno": "Turno"}), use_container_width=True)
    with colB:
        st.subheader("Inativos")
        st.dataframe(df_inativos[["id", "nome", "turno"]].rename(columns={"id": "ID", "nome": "Nome", "turno": "Turno"}), use_container_width=True)

    with st.expander("Ativar/Inativar colaboradores", expanded=False):
        ids_ativos = df_ativos["id"].tolist()
        ids_inativos = df_inativos["id"].tolist()
        ids_para_inativar = st.multiselect("Selecionar IDs para INATIVAR", ids_ativos)
        ids_para_ativar = st.multiselect("Selecionar IDs para ATIVAR", ids_inativos)
        if st.button("Aplicar alterações"):
            atualizar_ativo_colaboradores(ids_para_inativar, ids_para_ativar)
            st.success("Alterações aplicadas.")
            st.rerun()


# ---- Compat: reaproveita a nova tela de lançamento
def pagina_preenchimento():
    return pagina_lancamento_diario()



def pagina_relatorios_globais():
    st.markdown("### Relatórios Globais (todos os setores/turnos)")
    col1, col2 = st.columns(2)
    with col1:
        dt_ini = st.date_input("Data inicial", value=periodo_por_data(date.today())[0])
    with col2:
        dt_fim = st.date_input("Data final", value=periodo_por_data(date.today())[1])

    if st.button("Gerar relatório"):
        conn = get_conn()
        df = pd.read_sql_query(
            """
            SELECT c.nome AS colaborador, p.data, p.status, p.setor, p.turno, p.leader_nome
              FROM presencas p JOIN colaboradores c ON c.id = p.colaborador_id
             WHERE date(p.data) BETWEEN date(?) AND date(?)
             ORDER BY p.setor, p.turno, c.nome, p.data
            """,
            conn,
            params=(dt_ini.isoformat(), dt_fim.isoformat()),
        )
        conn.close()
        if df.empty:
            st.info("Sem dados no intervalo informado.")
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Baixar CSV Global",
                data=csv,
                file_name=f"presencas_global_{dt_ini}_{dt_fim}.csv",
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
            # só adiciona se não existir
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
    """Importa/atualiza turnos a partir de XLSX (com abas por setor ou com coluna SETOR)
    ou CSV (com coluna SETOR; se não tiver, usa setor_padrao).
    Retorna o total de linhas processadas.
    """
    import pandas as pd
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
# Página de Lançamento Diário (sem login)
# ------------------------------

def pagina_lancamento_diario():
    st.markdown("### Lançamento diário de presença (por setor)")
    # Agora com filtro de **Turno** ao lado do Setor
    colA, colB, colC, colD = st.columns([1,1,1,1])
    with colA:
        setor = st.selectbox("Setor", OPCOES_SETORES, index=0)
    with colB:
        turno_sel = st.selectbox("Turno", ["Todos"] + OPCOES_TURNOS, index=0)
    with colC:
        data_dia = st.date_input("Data do preenchimento", value=date.today())
    with colD:
        nome_preenchedor = st.text_input("Seu nome (opcional)")

    # Lista de colaboradores conforme filtro escolhido
    if turno_sel == "Todos":
        df_cols = listar_colaboradores_por_setor(setor, somente_ativos=True)
    else:
        df_cols = listar_colaboradores_setor_turno(setor, turno_sel, somente_ativos=True)

    if len(df_cols) == 0:
        st.warning("Nenhum colaborador cadastrado para este filtro.")
        st.stop()

    # Monta base de 1 dia
    iso = data_dia.isoformat()
    base = pd.DataFrame({"Colaborador": df_cols["nome"].tolist(), iso: ""}, dtype="object")

    # Preenche existentes
    pres = carregar_presencas(df_cols["id"].tolist(), data_dia, data_dia)
    mapa = dict(zip(df_cols["nome"], df_cols["id"]))
    base = aplicar_status_existentes(base, pres, mapa)

    cfg = {
        "Colaborador": st.column_config.TextColumn("Colaborador", disabled=True),
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
# Página de Turnos (edição em massa)
# ------------------------------

def pagina_turnos():
    st.markdown("### Turnos por colaborador (edição rápida)")
    setor_sel = st.selectbox("Setor", ["Todos"] + OPCOES_SETORES, index=0)

    if setor_sel == "Todos":
        df = listar_todos_colaboradores(somente_ativos=False)
    else:
        df = listar_colaboradores_por_setor(setor_sel, somente_ativos=False)

    if df.empty:
        st.info("Sem colaboradores cadastrados.")
        return

    df_view = df.sort_values(["setor", "nome"]).reset_index(drop=True)[["id", "nome", "setor", "turno", "ativo"]]
    df_view = df_view.rename(columns={"id": "ID", "nome": "Nome", "setor": "Setor", "turno": "Turno", "ativo": "Ativo"})

    cfg = {
        "ID": st.column_config.NumberColumn("ID", disabled=True),
        "Nome": st.column_config.TextColumn("Nome", disabled=True),
        "Setor": st.column_config.TextColumn("Setor", disabled=True),
        "Turno": st.column_config.SelectboxColumn("Turno", options=OPCOES_TURNOS),
        "Ativo": st.column_config.CheckboxColumn("Ativo"),
    }

    editado = st.data_editor(
        df_view,
        hide_index=True,
        use_container_width=True,
        column_config=cfg,
        key="editor_turnos",
    )

    if st.button("Salvar alterações de turno"):
        # Compara com original e atualiza só onde mudou
        orig = df_view
        merged = editado.merge(orig, on=["ID"], suffixes=("_novo", "_orig"))
        alterados = merged[merged["Turno_novo"] != merged["Turno_orig"]]
        for _, r in alterados.iterrows():
            atualizar_turno_colaborador(int(r["ID"]), r["Turno_novo"])
        if len(alterados) == 0:
            st.info("Nenhuma mudança de turno detectada.")
        else:
            st.success(f"{len(alterados)} colaborador(es) atualizado(s).")
            st.rerun()

# ------------------------------
# Roteamento (sem login)
# ------------------------------

st.sidebar.title("Menu")
escolha = st.sidebar.radio("Navegação", ["Lançamento diário", "Colaboradores", "Turnos", "Relatórios"], index=0)

with st.sidebar.expander("⚙️ Admin"):
    coladm1, coladm2 = st.columns([1,1])
    if coladm1.button("Carregar lista inicial de colaboradores"):
        seed_colaboradores_iniciais(turno_default="1°")
        st.success("Seed aplicado (somente adiciona quem não existe).")

    # Importar/atualizar turnos a partir de um arquivo (xlsx/csv)
    up = st.file_uploader("Importar turnos (xlsx/csv)", type=["xlsx", "xls", "csv"], key="up_turnos")
    setor_default = st.selectbox("Se o CSV não tiver coluna SETOR, aplicar a:", ["(obrigatório se CSV sem SETOR)"] + OPCOES_SETORES, index=0)
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
elif escolha == "Turnos":
    pagina_turnos()
else:
    pagina_relatorios_globais()

# Fim do arquivo
