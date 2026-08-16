import os
import sys
from pathlib import Path
from django.core.asgi import get_asgi_application

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for path in (PROJECT_ROOT / "core", PROJECT_ROOT / "backend" / "apps"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "university_verify.settings")
application = get_asgi_application()
