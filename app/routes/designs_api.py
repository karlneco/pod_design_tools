import json
from pathlib import Path
from PIL import Image

from flask import Blueprint, jsonify, request, send_from_directory, send_file
from werkzeug.utils import secure_filename

from .. import Config
from ..extensions import store, printify_client
from ..services.openai_svc import suggest_colors, suggest_metadata
from ..utils.mockups import generate_mockups_for_design
from .shopify_api import _generate_shopify_mockups_for_product

bp = Blueprint("designs_api", __name__)

DEFAULT_MOCKUP_TEMPLATE = "assets/mockups/flatlay_white_tee.png"
DEFAULT_MOCKUP_PLACEMENT = {"x": 900, "y": 1100, "max_w": 2100, "max_h": 2400}
DEFAULT_MOCKUP_SCALE = 1.0


def _mockup_sidecar_path(slug: str) -> Path:
    return Config.DATA_DIR / "designs" / slug / "mockup_placement.json"


def _load_mockup_sidecar(slug: str) -> dict | None:
    path = _mockup_sidecar_path(slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalized_mockup_settings(payload: dict | None) -> dict:
    payload = payload or {}
    template = payload.get("template") or DEFAULT_MOCKUP_TEMPLATE
    template_path = _resolve_template_path(template)
    if not template_path.exists():
        template = _default_template_path()
        template_path = _resolve_template_path(template)
    placements = payload.get("placements") or {"center": DEFAULT_MOCKUP_PLACEMENT.copy()}
    center = placements.get("center") or DEFAULT_MOCKUP_PLACEMENT.copy()
    clean_center = {}
    for key in ("x", "y", "max_w", "max_h"):
        val = center.get(key, DEFAULT_MOCKUP_PLACEMENT[key])
        try:
            val = int(round(float(val)))
        except (TypeError, ValueError):
            val = DEFAULT_MOCKUP_PLACEMENT[key]
        if val <= 0:
            val = DEFAULT_MOCKUP_PLACEMENT[key]
        clean_center[key] = val
    scale = payload.get("scale", DEFAULT_MOCKUP_SCALE)
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = DEFAULT_MOCKUP_SCALE
    if scale <= 0:
        scale = DEFAULT_MOCKUP_SCALE
    return {"template": template, "placements": {"center": clean_center}, "scale": scale}


def _resolve_template_path(template: str) -> Path:
    p = Path(template)
    if not p.is_absolute():
        p = (Config.BASE_DIR / p).resolve()
    return p


def _template_default_placement(template: str) -> dict:
    try:
        p = _resolve_template_path(template)
        if p.exists():
            with Image.open(p) as im:
                w, h = im.size
                return {"x": w // 2, "y": h // 2, "max_w": int(w * 0.5), "max_h": int(h * 0.5)}
    except Exception:
        pass
    return DEFAULT_MOCKUP_PLACEMENT.copy()


def _default_template_path() -> str:
    root = Config.ASSETS_DIR / "mockups"
    if root.exists():
        try:
            files = []
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in Config.ALLOWED_EXTS:
                    files.append(p)
            if files:
                first = sorted(files)[0]
                try:
                    return str(first.relative_to(Config.BASE_DIR))
                except Exception:
                    return str(first)
        except Exception:
            pass
    return DEFAULT_MOCKUP_TEMPLATE


def _mockup_out_dir_for_slug(slug: str) -> Path:
    if slug.startswith("shopify-"):
        product_id = slug.split("shopify-", 1)[1]
        return Config.PRODUCT_MOCKUPS_DIR / product_id
    return Config.MOCKUPS_DIR / slug


def _template_stem_index() -> dict[str, str]:
    index: dict[str, str] = {}
    root = Config.ASSETS_DIR / "mockups"
    if not root.exists():
        return index
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in Config.ALLOWED_EXTS:
            index[p.stem.lower()] = str(p.relative_to(Config.BASE_DIR))
    return index


def _infer_templates_from_out_dir(out_dir: Path) -> list[str]:
    if not out_dir.exists():
        return []
    stem_index = _template_stem_index()
    templates: list[str] = []
    for p in sorted(out_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in Config.ALLOWED_EXTS:
            continue
        stem = p.stem
        if stem.startswith("mockup_"):
            stem = stem[len("mockup_"):]
        key = stem.lower()
        if key in stem_index:
            templates.append(stem_index[key])
    return templates


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


@bp.get("/designs/<slug>/image")
def get_design_image(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    raw_path = Path(design.get("design_png_path", ""))
    if not raw_path.is_absolute():
        raw_path = (Config.BASE_DIR / raw_path).resolve()
    if not raw_path.exists() or not raw_path.is_file():
        return jsonify({"error": "Design image not found"}), 404
    if raw_path.suffix.lower() not in Config.ALLOWED_EXTS:
        return jsonify({"error": "Unsupported design image type"}), 400
    return send_file(raw_path)


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
    sidecar = _load_mockup_sidecar(slug) or {}
    merged = _normalized_mockup_settings({**sidecar, **config})
    templates = config.get("templates") or merged.get("templates")
    out_dir = _mockup_out_dir_for_slug(design["slug"])

    # Special handling for Shopify products: use per-color design sources from Printify
    if design["slug"].startswith("shopify-"):
        product_id = design["slug"].split("shopify-", 1)[1]
        try:
            out_paths = _generate_shopify_mockups_for_product(
                product_id,
                placements=merged.get("placements", {}),
                scale=merged.get("scale", 1.0),
            )
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": f"Mockup generation failed: {e}"}), 500
    else:
        if not templates:
            templates = _infer_templates_from_out_dir(out_dir)
        if not templates:
            templates = [merged["template"]]
        out_paths = generate_mockups_for_design(
            design_png_path=design["design_png_path"],
            templates=templates,
            placements=merged.get("placements", {}),
            out_dir=out_dir,
            scale=merged.get("scale", 1.0),
        )
    if design["slug"].startswith("shopify-"):
        renamed = []
        for p in out_paths:
            path = Path(p)
            name = path.name
            if name.startswith("mockup_"):
                final_path = path.parent / name[len("mockup_"):]
                try:
                    if final_path.exists():
                        final_path.unlink(missing_ok=True)
                    path.replace(final_path)
                    renamed.append(final_path)
                    continue
                except Exception:
                    renamed.append(path)
                    continue
            renamed.append(path)
        out_paths = renamed
    design["status"]["mockups_generated"] = True
    design.setdefault("assets", {})["mockups"] = [str(p) for p in out_paths]
    store.upsert("designs", slug, design)
    return jsonify({"mockups": [str(p) for p in out_paths]})


@bp.get("/designs/<slug>/mockup-placement")
def get_mockup_placement(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    data = _load_mockup_sidecar(slug)
    if not data:
        template = _default_template_path()
        data = {
            "template": template,
            "placements": {"center": _template_default_placement(template)},
            "scale": DEFAULT_MOCKUP_SCALE,
        }
    else:
        if not data.get("template"):
            data["template"] = _default_template_path()
        if not data.get("placements"):
            data["placements"] = {"center": _template_default_placement(data["template"])}
    payload = _normalized_mockup_settings(data)
    return jsonify(payload)


@bp.post("/designs/<slug>/mockup-placement")
def save_mockup_placement(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    payload = _normalized_mockup_settings(request.json)
    path = _mockup_sidecar_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return jsonify(payload)


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
