from pathlib import Path

from fastapi.templating import Jinja2Templates

from portal.formato import registrar

templates = Jinja2Templates(directory=str(Path(__file__).parent / "plantillas"))
registrar(templates)
