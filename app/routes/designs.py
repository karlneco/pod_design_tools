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
