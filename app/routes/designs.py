from flask import Blueprint, send_from_directory
from pathlib import Path as _P

bp = Blueprint("designs_pages", __name__)


@bp.get("/designs/<product_id>/<which>")
def serve_design_file(product_id, which):
    """
    Serves the saved design file (light|dark) if present under data/designs/<product_id>/.
    """
    base = _P("data/designs") / str(product_id)
    if which not in ("light", "dark"):
        return "Not found", 404
    for p in base.glob(f"{which}.*"):
        # Only allow image extensions
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return send_from_directory(base, p.name)  # /designs/<id>/<which> -> actual file
    return "Not found", 404


@bp.get("/designs/<slug>/lifestyle/<path:filename>")
def serve_lifestyle_file(slug: str, filename: str):
    base = (_P("data/designs") / str(slug) / "lifestyle").resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        return "Not found", 404
    if not target.exists() or not target.is_file():
        return "Not found", 404
    if target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".json"}:
        return "Not found", 404
    return send_from_directory(target.parent, target.name)
