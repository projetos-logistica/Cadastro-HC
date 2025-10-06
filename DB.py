# DB.py
import os
import pyodbc
from typing import Dict

# ---------------------------
# Configuração (use .env/ambiente)
# ---------------------------
CONFIG: Dict[str, str] = {
    "SERVER": os.getenv("DB_SERVER", "192.168.9.200"),
    "DATABASE": os.getenv("DB_NAME", "DbLogistica"),
    "UID": os.getenv("DB_USER", "Logistica_OPCD"),
    "PWD": os.getenv("DB_PASS", "Log1_Op@CD123"),
    # Lista de drivers aceitos em ordem de preferência
    "PREFERRED_DRIVERS": [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
    ],
    # Outras opções úteis
    "CONNECT_TIMEOUT": os.getenv("DB_CONNECT_TIMEOUT", "5"),  # segundos
    "ENCRYPT": os.getenv("DB_ENCRYPT", "yes"),                # Driver 18 exige encrypt
    "TRUST_CERT": os.getenv("DB_TRUST_CERT", "yes"),          # ok se não usar CA corporativa
}

def get_config() -> Dict[str, str]:
    return CONFIG.copy()

def _pick_driver() -> str:
    """Escolhe o primeiro driver disponível da lista de preferência."""
    installed = set(pyodbc.drivers())
    for drv in CONFIG["PREFERRED_DRIVERS"]:
        if drv in installed:
            return drv
    raise RuntimeError(
        "Nenhum driver ODBC do SQL Server compatível foi encontrado.\n"
        f"Instalados: {sorted(installed)}\n"
        "Instale, por exemplo: 'ODBC Driver 18 for SQL Server' (x64) "
        "ou ajuste o nome do driver na connection string."
    )

def _make_cnxn_string(driver: str) -> str:
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={CONFIG['SERVER']};"
        f"DATABASE={CONFIG['DATABASE']};"
        f"UID={CONFIG['UID']};"
        f"PWD={CONFIG['PWD']};"
        f"Encrypt={CONFIG['ENCRYPT']};"
        f"TrustServerCertificate={CONFIG['TRUST_CERT']};"
        f"Connection Timeout={CONFIG['CONNECT_TIMEOUT']};"
    )

def get_conn():
    """Retorna uma conexão pyodbc aberta com SQL Server."""
    drv = _pick_driver()
    cnxn_str = _make_cnxn_string(drv)
    return pyodbc.connect(cnxn_str)

def test_connection() -> bool:
    with get_conn() as cn:
        with cn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return True
