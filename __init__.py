
# start.py
import importlib

# importar módulos explicitamente (evita __init__ reexports)
TRI  = importlib.import_module("src.TestRailImporter")
TRIS = importlib.import_module("src.TestRailImporterSync")

from src.support.config_manager import ConfigManager
from src.support.logger import Logger

# --- Carregar config ---
config = ConfigManager()
try:
    config.load_config()
except Exception:
    config.build_config()

prefix = config.get('prefix') or ''
logger = Logger(config.get('debug'), prefix=prefix)

# --- Recuperar classes do módulo (com assert para erro claro) ---
assert hasattr(TRI,  "TestRailImporter"),      "src.TestRailImporter não expõe 'TestRailImporter'"
assert hasattr(TRIS, "TestRailImporterSync"),  "src.TestRailImporterSync não expõe 'TestRailImporterSync'"

ImporterClass = TRIS.TestRailImporterSync if config.get('sync') else TRI.TestRailImporter
importer = ImporterClass(config, logger)
importer.start()
