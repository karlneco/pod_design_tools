import json
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from .. import Config
from ..extensions import store, printify_client
from ..services.openai_svc import suggest_colors, suggest_metadata
from ..utils.mockups import generate_mockups_for_design

bp = Blueprint("designs_api", __name__)


@bp.get("/designs")
def list_designs():
    return jsonify(store.list("designs"))


@bp.post("/designs")
def create_design():
    payload = request.json or {}
    # Minimal validation
    required = ["slug", "title", "design_png_path"]
    for r in required:
        if r not in payload:
            return jsonify({"error": f"Missing field: {r}"}), 400
    # Ensure we don't copy design files; just reference path as requested
    design = {
        "slug": payload["slug"],
        "title": payload["title"],
        "design_png_path": payload["design_png_path"],
        "collections": payload.get("collections", []),
        "tags": payload.get("tags", []),
        "notes": payload.get("notes", ""),
        "status": {
            "mockups_generated": False,
            "product_created_printify": False,
            "published_shopify": False,
        },
        "generated": {
            "title": None,
            "description": None,
            "keywords": [],
            "colors": [],
        },
        "metadata": payload.get("metadata", {}),
    }
    store.upsert("designs", design["slug"], design)
    return jsonify(design), 201


@bp.post("/designs/upload")
def upload_designs():
    """
    Multipart form-data:
      - product_id (text)
      - light (file) optional
      - dark  (file) optional
    Saves to data/designs/<product_id>/orig/<original_filename>
    Writes data/designs/<product_id>/manifest.json mapping roles -> file path.
    """
    product_id = request.form.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    base = Path("data/designs") / str(product_id)
    base.mkdir(parents=True, exist_ok=True)

    manifest_path = base / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    saved = {}
    ALLOWED = {".png", ".jpg", ".jpeg", ".webp"}

    for role in ("light", "dark"):
        f = request.files.get(role)
        if not f or not f.filename:
            continue
        # sanitize and save under original filename
        secure_name = secure_filename(f.filename)
        ext = Path(secure_name).suffix.lower()
        if ext not in ALLOWED:
            return jsonify({"error": f"{role} must be one of {sorted(ALLOWED)}"}), 400

        out = base / secure_name
        f.save(out)

        # update manifest
        manifest[role] = {
            "file": secure_name                 # e.g. "MyCoolArt.png"
        }
        saved[role] = str(out)

    # persist manifest if changed
    if saved:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return jsonify({"ok": True, "saved": saved, "manifest": manifest})


@bp.get("/designs/<slug>")
def get_design(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    return jsonify(design)


@bp.patch("/designs/<slug>")
def update_design(slug):
    updates = request.json or {}
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    design.update(updates)
    store.upsert("designs", slug, design)
    return jsonify(design)


@bp.post("/designs/<slug>/ai/colors")
def ai_colors(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    colors = suggest_colors(
        design_title=design.get("title"),
        collections=design.get("collections", []),
        notes=design.get("notes", ""),
    )
    design["generated"]["colors"] = colors
    store.upsert("designs", slug, design)
    return jsonify({"colors": colors})


# -----------------------------
# AI: Titles/Descriptions/Keywords & Color Suggestions
# -----------------------------
@bp.post("/designs/<slug>/ai/metadata")
def ai_metadata(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    local_docs = {
        "personas_pdf": str(Config.BASE_DIR / "KD Personas.pdf"),
        "principles": str(Config.BASE_DIR / "T-Shirt Design Core Principals.txt"),
        "policies": str(Config.BASE_DIR / "policies.md"),
    }

    meta = suggest_metadata(
        title_hint=design.get("title"),
        collections=design.get("collections", []),
        notes=design.get("notes", ""),
        docs_paths=local_docs,
    )
    design["generated"].update(meta)
    store.upsert("designs", slug, design)
    return jsonify(meta)


# -----------------------------
# Mockup generation (flat-lay composites via Pillow)
# -----------------------------
@bp.post("/designs/<slug>/mockups")
def generate_mockups(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    config = request.json or {}
    out_paths = generate_mockups_for_design(
        design_png_path=design["design_png_path"],
        templates=config.get("templates", []),
        placements=config.get("placements", {}),
        out_dir=Config.MOCKUPS_DIR / design["slug"],
        scale=config.get("scale", 1.0),
    )
    design["status"]["mockups_generated"] = True
    design.setdefault("assets", {})["mockups"] = [str(p) for p in out_paths]
    store.upsert("designs", slug, design)
    return jsonify({"mockups": [str(p) for p in out_paths]})


# -----------------------------
# Printify: create product & publish to Shopify (via Printify)
# -----------------------------
@bp.post("/designs/<slug>/printify/create-product")
def printify_create_product(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    created = printify_client.create_product(
        product_spec={
            "title": design["generated"].get("title") or design["title"],
            "description": design["generated"].get("description") or "",
            "tags": design["generated"].get("keywords") or design.get("tags", []),
            "blueprint_id": payload.get("blueprint_id"),
            "print_provider_id": payload.get("print_provider_id"),
            "variants": payload.get("variants", []),
            "print_areas": payload.get("print_areas", []),
        },
    )
    design["status"]["product_created_printify"] = True
    design.setdefault("integrations", {})["printify_product"] = created
    store.upsert("designs", slug, design)
    return jsonify(created)


@bp.post("/designs/<slug>/printify/publish")
def printify_publish(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    result = printify_client.publish_to_shopify(
        product_id=payload.get("product_id"),
        publish_details=payload.get("publish_details", {}),
    )
    design["status"]["published_shopify"] = True
    store.upsert("designs", slug, design)
    return jsonify(result)


@bp.get("/designs/<product_id>/<which>")
def serve_design_file(product_id, which):
    base = Path("data/designs") / str(product_id)
    if which not in ("light", "dark"):
        return "Not found", 404

    manifest_path = base / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest.get(which)
            if entry and entry.get("file"):
                target = base / entry["file"]
                if target.exists() and target.is_file():
                    return send_from_directory(target.parent, target.name)
        except Exception:
            pass

    # fallback: legacy light.* / dark.* layout
    for p in base.glob(f"{which}.*"):
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            return send_from_directory(p.parent, p.name)
    return "Not found", 404
