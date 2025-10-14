# run_streamlit.py
import sys
from pathlib import Path
import streamlit.web.cli as stcli

def app_file_path() -> str:
    # Quando empacotado, PyInstaller usa a pasta temporária _MEIPASS
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    # os .py serão adicionados com --add-data (abaixo)
    return str(base / "cadastro_hc.py")

if __name__ == "__main__":
    sys.argv = [
        "streamlit", "run", app_file_path(),
        "--global.developmentMode=false",
        "--server.headless=true",
        # "--server.port=8501",  # fixe aqui se quiser
    ]
    sys.exit(stcli.main())
