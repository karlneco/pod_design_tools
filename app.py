import base64
import mimetypes
import os
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime, timezone
from openai import OpenAI
import base64, mimetypes, json, os
from flask import send_from_directory
from pathlib import Path as _P
from storage import JsonStore
from services.printify import PrintifyClient
from services.shopify import ShopifyClient
from services.openai_svc import suggest_metadata, suggest_colors
from mockups.composer import generate_mockups_for_design
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
MOCKUPS_DIR = ASSETS_DIR / "mockups"
GENERATED_MOCKUPS_DIR = BASE_DIR / "generated_mockups"
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

DATA_DIR.mkdir(exist_ok=True)
(ASSETS_DIR / "mockups").mkdir(parents=True, exist_ok=True)
MOCKUPS_DIR.mkdir(exist_ok=True)

store = JsonStore(DATA_DIR)
printify = PrintifyClient()
shopify = ShopifyClient(
    store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"),
    admin_token=os.getenv("SHOPIFY_ADMIN_TOKEN"),
    api_version=os.getenv("SHOPIFY_API_VERSION", "2024-10"),
)

PRODUCTS_COLLECTION = "shopify_products"
PRINTIFY_PRODUCTS_COLLECTION = "printify_products"


@app.template_filter("todatetime")
def _todatetime(value):
    """Convert ISO string to datetime for pretty_date macro."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/products")
def products_page():
    products = store.list(PRODUCTS_COLLECTION)

    # Sort newest first by available timestamp
    def _ts(p):
        t = p.get("created_at") or p.get("updated_at")
        if not t:
            return datetime.min
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    products = sorted(products, key=_ts, reverse=True)
    return render_template("products.html", products=products, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


@app.get("/printify")
def printify_page():
    items = store.list(PRINTIFY_PRODUCTS_COLLECTION)

    # show newest first if we have dates
    def _ts(p):
        return p.get("updated_at") or p.get("created_at") or ""

    items = sorted(items, key=_ts, reverse=True)
    return render_template("printify.html", items=items, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


@app.get("/printify/new")
def printify_new():
    # Use cached Printify products and filter titles containing 'template'
    items = store.list(PRINTIFY_PRODUCTS_COLLECTION)
    templates = []
    for p in items:
        title = (p.get("title") or "").lower()
        if "template" in title:
            templates.append(p)
    # Sort alphabetically
    templates = sorted(templates, key=lambda x: (x.get("title") or "").lower())
    return render_template("printify_new.html", templates=templates)


@app.get("/designs/<product_id>/<which>")
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


@app.get("/printify/edit/<product_id>")
def printify_edit(product_id):
    try:
        full = printify.get_product(product_id)
    except Exception as e:
        return f"Failed to load product: {e}", 400

    # Log raw JSON for debugging
    try:
        import json as _json
        app.logger.info("Printify product %s:\n%s", product_id, _json.dumps(full, indent=2, ensure_ascii=False))
    except Exception:
        pass

    # === Build provider color catalog (title -> {title,hex}, variant_id -> {title,hex}) ===
    all_colors = []
    color_by_title = {}
    color_by_variant_id = {}
    for opt in (full.get("options") or []):
        if (opt.get("type") == "color") or (str(opt.get("name","")).lower() == "colors"):
            for v in (opt.get("values") or []):
                hexes = v.get("colors") or []
                item = {
                    "id": v.get("id"),
                    "title": v.get("title"),
                    "hex": (hexes[0] if hexes else "#dddddd")
                }
                all_colors.append(item)
                color_by_title[str(v.get("title"))] = item

    # Map variant_id -> color via variant["options"] or title fallback
    def _variant_color_tuple(var: dict) -> tuple[str,str]:
        # returns (title, hex)
        # options can be dict or list
        ctitle = None
        opts = var.get("options")
        if isinstance(opts, dict):
            ctitle = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
        elif isinstance(opts, list):
            for o in opts:
                try:
                    name = (o.get("name") or "").strip().lower()
                    if name in ("color", "colour"):
                        ctitle = o.get("value") or o.get("title")
                        break
                except AttributeError:
                    pass
        if not ctitle:
            t = var.get("title") or ""
            ctitle = t.split(" / ")[0] if " / " in t else None
        ctitle = str(ctitle) if ctitle else None
        hexv = color_by_title.get(ctitle, {}).get("hex", "#dddddd") if ctitle else "#dddddd"
        return (ctitle or "—", hexv)

    for var in (full.get("variants") or []):
        vid = var.get("id")
        if vid is None:
            continue
        ctitle, chex = _variant_color_tuple(var)
        color_by_variant_id[int(vid)] = {"title": ctitle, "hex": chex}

    # Compute template colors actually used, with fallback to print_areas.variant_ids
    used_titles = set()
    variants = full.get("variants") or []

    # 1) Primary: variants where is_enabled != False
    for var in variants:
        if var.get("is_enabled", True) is False:
            continue
        used_titles.add(_variant_color_tuple(var)[0])

    # 2) Fallback: union of colors referenced by any print_area.variant_ids
    if not used_titles:
        for pa in (full.get("print_areas") or []):
            for vid in (pa.get("variant_ids") or []):
                cinfo = color_by_variant_id.get(int(vid))
                if cinfo and cinfo.get("title"):
                    used_titles.add(cinfo["title"])

    template_colors_used = []
    for title in sorted(used_titles, key=lambda s: s.lower()):
        if title in color_by_title:
            template_colors_used.append(color_by_title[title])
        else:
            template_colors_used.append({"id": None, "title": title, "hex": "#dddddd"})

    # Dropdown list: all available colors (alphabetical)
    available_colors = sorted(all_colors, key=lambda c: (c["title"] or "").lower())

    # === Group existing placeholder images by garment lightness → panels ===
    def _hex_to_rgb(h):
        h = (h or "").lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return (221, 221, 221)

    def _luma(hexv):
        r, g, b = _hex_to_rgb(hexv)
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0

    # Treat exact names as hard overrides, then fall back to luminance
    def _is_dark_garment(title: str, hexv: str) -> bool:
        t = (title or "").strip().lower()
        if t in ("black", "charcoal", "dark heather", "navy", "forest green"):
            return True
        if t in ("white", "natural", "cream", "ivory", "silver", "ash"):
            return False
        return _luma(hexv) < 0.35

    def _is_light_garment(title: str, hexv: str) -> bool:
        t = (title or "").strip().lower()
        if t in ("white", "natural", "cream", "ivory", "silver", "ash"):
            return True
        if t in ("black", "charcoal", "dark heather", "navy", "forest green"):
            return False
        return _luma(hexv) > 0.65

    from collections import Counter

    light_panel_colors = {}  # hex -> title (for DARK garments → Light-design panel)
    dark_panel_colors = {}  # hex -> title (for LIGHT garments → Dark-design panel)
    light_img_votes = Counter()  # src -> votes from dark garments
    dark_img_votes = Counter()  # src -> votes from light garments

    for pa in (full.get("print_areas") or []):
        vids = [int(v) for v in (pa.get("variant_ids") or [])]
        colors_for_area = [color_by_variant_id.get(v) for v in vids if v in color_by_variant_id]
        if not colors_for_area:
            continue

        # Collect all usable image srcs from this area
        area_srcs = []
        for ph in (pa.get("placeholders") or []):
            for img in (ph.get("images") or []):
                if isinstance(img, dict) and img.get("src"):
                    area_srcs.append(img["src"])

        # Tally colors for this area into light/dark garment buckets
        dark_bucket = []
        light_bucket = []
        for c in colors_for_area:
            if not c: continue
            title, hexv = c["title"], c["hex"]
            if _is_dark_garment(title, hexv):
                dark_bucket.append(c)  # dark garments → Light-design panel
                light_panel_colors[hexv] = title
            if _is_light_garment(title, hexv):
                light_bucket.append(c)  # light garments → Dark-design panel
                dark_panel_colors[hexv] = title

        # Vote images for each panel by majority of colors this area covers
        if area_srcs:
            if dark_bucket:
                for s in area_srcs:
                    light_img_votes[s] += len(dark_bucket)
            if light_bucket:
                for s in area_srcs:
                    dark_img_votes[s] += len(light_bucket)

    # Pick the most-voted image per panel
    light_panel_image = None
    dark_panel_image = None
    if light_img_votes:
        light_panel_image = light_img_votes.most_common(1)[0][0]
    if dark_img_votes:
        dark_panel_image = dark_img_votes.most_common(1)[0][0]

    # Saved design URLs if present locally
    from pathlib import Path
    base = Path("data/designs") / str(product_id)
    def _first_url(which):
        for pth in base.glob(f"{which}.*"):
            if pth.suffix.lower() in {".png",".jpg",".jpeg",".webp"}:
                return f"/designs/{product_id}/{which}"
        return None

    ctx = {
        "id": str(full.get("id") or full.get("_id") or product_id),
        "title": full.get("title") or full.get("name") or "",
        "description": full.get("description") or "",
        "raw": full,

        "template_colors_used": template_colors_used,
        "available_colors": available_colors,  # dropdown list, alphabetical

        # local uploads (override previews if present)
        "light_url": _first_url("light") or light_panel_image,
        "dark_url":  _first_url("dark")  or dark_panel_image,

        # initial saved pills per panel (from existing product mapping)
        "initial_saved_light": [{"title": t, "hex": h} for h,t in sorted(light_panel_colors.items(), key=lambda kv: kv[1].lower())],
        "initial_saved_dark":  [{"title": t, "hex": h} for h,t in sorted(dark_panel_colors.items(),  key=lambda kv: kv[1].lower())],
    }
    return render_template("printify_edit.html", p=ctx)



# -----------------------------
# Designs — CRUD with JSON files
# -----------------------------
@app.get("/api/designs")
def list_designs():
    return jsonify(store.list("designs"))


@app.post("/api/designs")
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


@app.get("/api/designs/<slug>")
def get_design(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    return jsonify(design)


@app.patch("/api/designs/<slug>")
def update_design(slug):
    updates = request.json or {}
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    design.update(updates)
    store.upsert("designs", slug, design)
    return jsonify(design)


# -----------------------------
# AI: Titles/Descriptions/Keywords & Color Suggestions
# -----------------------------
@app.post("/api/designs/<slug>/ai/metadata")
def ai_metadata(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    local_docs = {
        "personas_pdf": str(BASE_DIR / "KD Personas.pdf"),
        "principles": str(BASE_DIR / "T-Shirt Design Core Principals.txt"),
        "policies": str(BASE_DIR / "policies.md"),
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


@app.post("/api/designs/<slug>/ai/colors")
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
# Mockup generation (flat-lay composites via Pillow)
# -----------------------------
@app.post("/api/designs/<slug>/mockups")
def generate_mockups(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    config = request.json or {}
    out_paths = generate_mockups_for_design(
        design_png_path=design["design_png_path"],
        templates=config.get("templates", []),
        placements=config.get("placements", {}),
        out_dir=MOCKUPS_DIR / design["slug"],
        scale=config.get("scale", 1.0),
    )
    design["status"]["mockups_generated"] = True
    design.setdefault("assets", {})["mockups"] = [str(p) for p in out_paths]
    store.upsert("designs", slug, design)
    return jsonify({"mockups": [str(p) for p in out_paths]})


# -----------------------------
# Printify: create product & publish to Shopify (via Printify)
# -----------------------------
@app.post("/api/designs/<slug>/printify/create-product")
def printify_create_product(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    created = printify.create_product(
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


@app.post("/api/designs/<slug>/printify/publish")
def printify_publish(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    result = printify.publish_to_shopify(
        product_id=payload.get("product_id"),
        publish_details=payload.get("publish_details", {}),
    )
    design["status"]["published_shopify"] = True
    store.upsert("designs", slug, design)
    return jsonify(result)


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@app.post("/api/shopify/products/<product_id>/images")
def shopify_upload_images(product_id):
    payload = request.json or {}
    image_paths = payload.get("image_paths", [])
    uploaded = shopify.upload_product_images(product_id, image_paths)
    return jsonify({"uploaded": uploaded})


@app.get("/api/products")
def api_list_products():
    return jsonify(store.list(PRODUCTS_COLLECTION))


@app.get("/api/printify/products")
def api_list_printify_products():
    return jsonify(store.list(PRINTIFY_PRODUCTS_COLLECTION))


@app.get("/api/printify/colors/<product_id>")
def api_printify_colors(product_id):
    """Return the distinct color names (and codes if present) available for this product’s blueprint/provider."""
    prod = printify.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if bp is None or pp is None:
        return jsonify({"error": "blueprint_id or print_provider_id missing on product"}), 400

    try:
        cat = printify.get_blueprint_provider_variants(bp, pp)
    except Exception as e:
        # If this provider doesn't serve this blueprint, show what's available
        try:
            provs = printify.list_blueprint_providers(bp)
        except Exception:
            provs = {}
        return jsonify({
            "error": f"Provider {pp} not found for blueprint {bp}.",
            "hint": "Use one of the providers listed for this blueprint.",
            "available_providers": provs
        }), 404

    # v1 endpoint returns a list or an object with 'variants'
    variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
    colors = set()
    color_map = {}  # color -> list of variant_ids
    for v in variants:
        opts = v.get("options") or {}
        color = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
        if color:
            colors.add(color)
            vid = v.get("id")
            if vid is not None:
                color_map.setdefault(color, []).append(vid)

    return jsonify({
        "blueprint_id": int(bp),
        "print_provider_id": int(pp),
        "colors": sorted(colors),
        "color_variants": color_map
    })


@app.post("/api/printify/templates/<product_id>/extract_colors")
def api_printify_extract_colors(product_id: str):
    """
    Load a template product from Printify and persist its color set (with hex)
    to assets/mockups/{blueprint_id}_{print_provider_id}/colors.json
    """
    try:
        from services.printify import PrintifyClient
        pci = PrintifyClient()
        tpl = pci.get_product(product_id)
    except Exception as e:
        return jsonify({"error": f"Failed to load product {product_id}", "detail": str(e)}), 400

    bp = tpl.get("blueprint_id")
    pp = tpl.get("print_provider_id")
    options = tpl.get("options") or []

    color_option = None
    for opt in options:
        # Printify uses type: "color" and often name: "Colors"
        if (opt.get("type") == "color") or (opt.get("name", "").lower() == "colors"):
            color_option = opt
            break

    if not color_option:
        return jsonify({"error": "No color option found on template product."}), 404

    values = color_option.get("values") or []

    # Normalize to a compact list of {id, title, hex}
    colors_out = []
    for v in values:
        hexes = v.get("colors") or []
        # Some entries may provide multiple hexes (very rare) – keep the first for UI,
        # but store all in 'hexes' as well.
        primary_hex = (hexes[0] if hexes else None)
        colors_out.append({
            "id": v.get("id"),
            "title": v.get("title"),
            "hex": primary_hex,
            "hexes": hexes
        })

    # Compose write path
    folder_name = f"{bp}_{pp}"
    folder = MOCKUPS_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / "colors.json"

    payload = {
        "blueprint_id": bp,
        "print_provider_id": pp,
        "option_name": color_option.get("name") or "Colors",
        "values": colors_out,
        "generated_from_product_id": tpl.get("id"),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Save
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return jsonify({
        "ok": True,
        "message": "Colors extracted.",
        "path": str(out_path.relative_to(BASE_DIR)),
        "count": len(colors_out)
    })


@app.post("/api/designs/upload")
def upload_designs():
    """
    Multipart form-data:
      - product_id (text)
      - light (file) optional
      - dark (file) optional
    Saves to data/designs/<product_id>/{light|dark}<ext>
    """
    product_id = request.form.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    dest = Path("data/designs") / str(product_id)
    dest.mkdir(parents=True, exist_ok=True)

    saved = {}
    for key in ("light", "dark"):
        file = request.files.get(key)
        if not file or not file.filename:
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            return jsonify({"error": f"{key} must be one of {ALLOWED_EXTS}"}), 400
        out = dest / f"{key}{ext}"
        file.save(out)
        saved[key] = str(out)

    return jsonify({"ok": True, "saved": saved})



@app.post("/api/printify/products/duplicate")
def api_printify_duplicate():
    data = request.get_json(force=True) or {}
    product_id = data.get("product_id") or data.get("template_id")
    new_title = data.get("title") or "New Product"
    new_description = data.get("description") or ""

    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    pci = PrintifyClient()

    # 1) Fetch the template product
    template = pci.get_product(product_id=product_id)

    # 2) Create a new product using the “lean” payload (no SKUs, slim variants & print_areas)
    created = pci.duplicate_from_template(
        template=template,
        title=new_title,
        description=new_description,
        tags=template.get("tags", [])
    )

    return jsonify({"ok": True, "created": created}), 201


@app.post("/api/products/cache/update")
def update_products_cache():
    # Fetch all products from Shopify and normalize to our schema
    raw_products = shopify.list_all_products(limit=250)
    normalized = {}

    for p in raw_products:
        pid = str(p.get("id"))
        handle = p.get("handle")
        url = shopify.product_url(handle)
        product_type = p.get("product_type") or ""
        desc = p.get("body_html") or ""
        tags = [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()]
        status = p.get("status") or "unknown"
        created_at = p.get("created_at")
        updated_at = p.get("updated_at")

        # Primary image
        img = None
        if p.get("image") and p["image"].get("src"):
            img = p["image"]["src"]
        elif p.get("images"):
            img = p["images"][0].get("src") if p["images"] else None

        # Map image_id -> src for variant thumbnails
        images_map = {}
        for _im in (p.get("images") or []):
            if _im.get("id") and _im.get("src"):
                images_map[_im["id"]] = _im["src"]

        # Variants (color/size detection via option names)
        option_map = {opt.get("position"): opt.get("name", "") for opt in (p.get("options") or [])}
        color_pos = next((pos for pos, name in option_map.items() if name and name.lower() in ["color", "colour"]),
                         None)
        size_pos = next((pos for pos, name in option_map.items() if name and name.lower() == "size"), None)

        variants = []
        # track first thumbnail per color
        color_image_map = {}

        for v in (p.get("variants") or []):
            opts = [v.get(f"option{i}") for i in range(1, 4)]
            color = opts[(color_pos - 1)] if color_pos else None
            size = opts[(size_pos - 1)] if size_pos else None

            # pick image for this variant if available
            v_image_src = None
            v_image_id = v.get("image_id")
            if v_image_id and v_image_id in images_map:
                v_image_src = images_map[v_image_id]
            else:
                # Some payloads embed a direct URL or dict
                vimg = v.get("image") or v.get("preview")
                if isinstance(vimg, dict):
                    v_image_src = vimg.get("src") or vimg.get("url")
                elif isinstance(vimg, str):
                    v_image_src = vimg

            # remember the first image we see for each color (fallback later to primary)
            if color and color not in color_image_map:
                color_image_map[color] = v_image_src  # may be None for now

            variants.append({
                "id": v.get("id"),
                "title": v.get("title"),
                "sku": v.get("sku"),
                "color": color,
                "size": size,
                "price": v.get("price"),
                "available": v.get("available", True),
                "image": v_image_src,
            })

        # Build compact color variants list using first image per color (fallback to primary)
        color_variants = []
        seen = set()
        for color, cimg in color_image_map.items():
            if not color:
                continue
            color_variants.append({
                "color": color,
                "image": cimg or img  # fallback to product primary image
            })
            seen.add(color)

        # ensure we capture colors that had no image map but exist in variants
        for v in variants:
            c = v.get("color")
            if c and c not in seen:
                color_variants.append({"color": c, "image": v.get("image") or img})
                seen.add(c)

        normalized[pid] = {
            "id": pid,
            "title": p.get("title"),
            "url": url,
            "type": product_type,
            "description": desc,
            "tags": tags,
            "variants": variants,  # full variants preserved
            "color_variants": color_variants,  # new compact list for UI
            "primary_image": img,
            "handle": handle,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    # Save cache
    store.replace_collection(PRODUCTS_COLLECTION, normalized)
    return jsonify({"count": len(normalized)})


@app.post("/api/printify/products/cache/update")
def update_printify_products_cache():
    """Download Printify products, normalize, and store cache."""
    shop_id = (request.json or {}).get("shop_id") or os.getenv("PRINTIFY_SHOP_ID")
    if not shop_id:
        return jsonify({"error": "Missing shop_id (provide in body or set PRINTIFY_SHOP_ID)"}), 400

    normalized = {}
    page = 1
    total = 0

    while True:
        page_data = printify.list_products(page=page, limit=100)
        # API may return either {"data":[...], "last_page":N, ...} or {"products":[...]}
        data_list = page_data.get("data") or page_data.get("products") or []
        if not data_list:
            break

        for p in data_list:
            pid = str(p.get("id") or p.get("_id") or "")
            if not pid:
                continue

            title = p.get("title") or p.get("name") or ""
            # main image: tolerate dicts or plain strings; fallback to preview
            primary_image = None
            imgs = p.get("images") or []
            if imgs:
                first = imgs[0]
                if isinstance(first, dict):
                    primary_image = first.get("src") or first.get("url")
                elif isinstance(first, str):
                    primary_image = first
            if not primary_image:
                prv = p.get("preview")
                if isinstance(prv, dict):
                    primary_image = prv.get("src") or prv.get("url")
                elif isinstance(prv, str):
                    primary_image = prv

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            # tolerate dict or string shapes
            if isinstance(ext, dict):
                ext_handle = ext.get("handle") or ext.get("shopify_handle") or ext.get("product_handle")
            elif isinstance(ext, str):
                # in rare cases 'external' may just be the handle
                ext_handle = ext.strip()
            else:
                ext_handle = None

            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"

            # Publication status: simply whether we have a Shopify link
            published = bool(shopify_url)

            # Channel-specific shapes: list, dict, or strings
            if published is None:
                published = False
                sc_props = p.get("sales_channel_properties") or p.get("sales_channels") or []
                # If it's a dict, check values; if list, iterate items; if string, look for 'published'
                if isinstance(sc_props, dict):
                    for v in sc_props.values():
                        if (isinstance(v, dict) and _to_bool(v.get("published"))) or \
                                (isinstance(v, str) and "published" in v.lower()):
                            published = True
                            break
                elif isinstance(sc_props, list):
                    for sc in sc_props:
                        if isinstance(sc, dict):
                            if _to_bool(sc.get("published")):
                                published = True
                                break
                        elif isinstance(sc, str):
                            if "published" in sc.lower():
                                published = True
                                break
                elif isinstance(sc_props, str):
                    published = "published" in sc_props.lower()

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            ext_handle = ext.get("handle")
            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"
            else:
                # sometimes external id exists; if you store a mapping later, plug it here
                pass

            normalized[pid] = {
                "id": pid,
                "title": title,
                "primary_image": primary_image,
                "published": bool(published),
                "shopify_url": shopify_url,
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            total += 1

        # pagination end?
        last_page = page_data.get("last_page")
        current_page = page_data.get("current_page") or page
        if last_page and current_page < last_page:
            page += 1
            continue
        # Fallback: if no last_page info, stop after first page unless data == limit
        if not last_page and len(data_list) >= 100:
            page += 1
            continue
        break

    store.replace_collection(PRINTIFY_PRODUCTS_COLLECTION, normalized)
    return jsonify({"count": len(normalized)})


@app.post("/api/recommend/colors")
def recommend_colors():
    """
    JSON body:
      { "product_id": "..." }
    Backend will derive blueprint_id & print_provider_id and collect color names + hex.
    Uses any /data/designs/<product_id>/light|dark.* files if present.
    """

    body = request.get_json(force=True) or {}
    product_id = body.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    # 1) Load product to get bp/pp and inline colors, if present
    prod = printify.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if not bp or not pp:
        return jsonify({"error":"Product missing blueprint_id or print_provider_id"}), 400

    # 2) Try colors.json first
    from pathlib import Path as _P
    folder = ASSETS_DIR / f"{bp}_{pp}"
    colors_json = folder / "colors.json"
    colors = []
    if colors_json.exists():
        try:
            data = json.loads(colors_json.read_text(encoding="utf-8"))
            for v in (data.get("values") or []):
                colors.append({"id": v.get("id"), "title": v.get("title"), "hex": (v.get("hex") or "#dddddd")})
        except Exception:
            pass

    # 3) If empty, fall back to product.options[type=color].values[].colors[]
    if not colors:
        for opt in (prod.get("options") or []):
            if (opt.get("type") == "color") or (opt.get("name","").lower() == "colors"):
                for v in (opt.get("values") or []):
                    hexes = v.get("colors") or []
                    colors.append({"id": v.get("id"), "title": v.get("title"), "hex": (hexes[0] if hexes else None) or "#dddddd"})

    # 4) If still empty, final fallback to variants endpoint (no hex there, so use placeholders)
    if not colors:
        cat = printify.get_blueprint_provider_variants(bp, pp)
        variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
        # dedupe by color name, fabricate hex = '#dddddd'
        seen = set()
        for v in variants:
            name = (v.get("options") or {}).get("color") or (v.get("options") or {}).get("Color")
            if name and name not in seen:
                colors.append({"id": None, "title": name, "hex": "#dddddd"})
                seen.add(name)

    if not colors:
        return jsonify({"error":"Could not determine provider colors for this product"}), 400

    # 5) Load local design images if present
    base = Path("data/designs") / str(product_id)
    ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    light_file = next((p for p in base.glob("light.*") if p.suffix.lower() in ALLOWED_EXTS), None)
    dark_file  = next((p for p in base.glob("dark.*") if p.suffix.lower() in ALLOWED_EXTS), None)

    def as_image_part(path: Path):
        if not path: return None
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return {"type":"image_url","image_url":{"url": f"data:{mime};base64,{b64}"}}

    # 6) Build the GPT prompt with names + hex
    color_payload = [{"title": c["title"], "hex": c["hex"]} for c in colors]
    messages = [
        {"role":"system","content":"You are a product designer choosing garment colors that best complement a design image for DTG/DTF printing. Consider contrast, readability, and color harmony."},
        {"role":"user","content":[
            {"type":"text","text":(
                "Given the provided design images (light and/or dark versions) and this list of available shirt colors "
                "(each with hex), recommend up to 6 shirt colors total: 3 best for the LIGHT-design (if provided) and "
                "3 best for the DARK-design (if provided). Only choose from the provided colors. "
                "Return strict JSON with keys 'light' and 'dark', each an array like [{\"title\":\"Black\",\"hex\":\"#000000\"}, ...]."
                f"\n\nAvailable colors:\n{json.dumps(color_payload, ensure_ascii=False)}"
            )},
        ] + ([as_image_part(light_file)] if light_file else []) + ([as_image_part(dark_file)] if dark_file else [])}
    ]

    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    if not openai_api_key:
        return jsonify({"error":"OPENAI_API_KEY missing in .env"}), 400

    # 7) Call OpenAI and sanitize results to the available set
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        # normalize + filter to available titles
        avail_by_title = {c["title"]: c for c in color_payload}
        def _filter(side):
            out = []
            for c in (data.get(side) or []):
                t = c.get("title")
                if t in avail_by_title:
                    # keep our canonical hex (ignore model's hex just in case)
                    out.append({"title": t, "hex": avail_by_title[t]["hex"]})
                if len(out) >= 3: break
            return out
        return jsonify({"light": _filter("light"), "dark": _filter("dark"), "available_colors": color_payload})
    except Exception as e:
        return jsonify({"error": str(e)}), 400



# -----------------------------
# Static serve for generated mockups (for quick preview)
# -----------------------------
@app.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = MOCKUPS_DIR
    return send_from_directory(dirpath, filename)
import base64
import mimetypes
import os
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime, timezone
from openai import OpenAI
import base64, mimetypes, json, os
from flask import send_from_directory
from pathlib import Path as _P
from storage import JsonStore
from services.printify import PrintifyClient
from services.shopify import ShopifyClient
from services.openai_svc import suggest_metadata, suggest_colors
from mockups.composer import generate_mockups_for_design
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
MOCKUPS_DIR = ASSETS_DIR / "mockups"
GENERATED_MOCKUPS_DIR = BASE_DIR / "generated_mockups"
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

DATA_DIR.mkdir(exist_ok=True)
(ASSETS_DIR / "mockups").mkdir(parents=True, exist_ok=True)
MOCKUPS_DIR.mkdir(exist_ok=True)

store = JsonStore(DATA_DIR)
printify = PrintifyClient()
shopify = ShopifyClient(
    store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"),
    admin_token=os.getenv("SHOPIFY_ADMIN_TOKEN"),
    api_version=os.getenv("SHOPIFY_API_VERSION", "2024-10"),
)

PRODUCTS_COLLECTION = "shopify_products"
PRINTIFY_PRODUCTS_COLLECTION = "printify_products"


@app.template_filter("todatetime")
def _todatetime(value):
    """Convert ISO string to datetime for pretty_date macro."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


# -----------------------------
# Simple Pages
# -----------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/products")
def products_page():
    products = store.list(PRODUCTS_COLLECTION)

    # Sort newest first by available timestamp
    def _ts(p):
        t = p.get("created_at") or p.get("updated_at")
        if not t:
            return datetime.min
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    products = sorted(products, key=_ts, reverse=True)
    return render_template("products.html", products=products, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


@app.get("/printify")
def printify_page():
    items = store.list(PRINTIFY_PRODUCTS_COLLECTION)

    # show newest first if we have dates
    def _ts(p):
        return p.get("updated_at") or p.get("created_at") or ""

    items = sorted(items, key=_ts, reverse=True)
    return render_template("printify.html", items=items, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


@app.get("/printify/new")
def printify_new():
    # Use cached Printify products and filter titles containing 'template'
    items = store.list(PRINTIFY_PRODUCTS_COLLECTION)
    templates = []
    for p in items:
        title = (p.get("title") or "").lower()
        if "template" in title:
            templates.append(p)
    # Sort alphabetically
    templates = sorted(templates, key=lambda x: (x.get("title") or "").lower())
    return render_template("printify_new.html", templates=templates)


@app.get("/designs/<product_id>/<which>")
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


@app.get("/printify/edit/<product_id>")
def printify_edit(product_id):
    try:
        full = printify.get_product(product_id)
    except Exception as e:
        return f"Failed to load product: {e}", 400

    # Log raw JSON for debugging
    try:
        import json as _json
        app.logger.info("Printify product %s:\n%s", product_id, _json.dumps(full, indent=2, ensure_ascii=False))
    except Exception:
        pass

    # === Build provider color catalog (title -> {title,hex}, variant_id -> {title,hex}) ===
    all_colors = []
    color_by_title = {}
    color_by_variant_id = {}
    for opt in (full.get("options") or []):
        if (opt.get("type") == "color") or (str(opt.get("name","")).lower() == "colors"):
            for v in (opt.get("values") or []):
                hexes = v.get("colors") or []
                item = {
                    "id": v.get("id"),
                    "title": v.get("title"),
                    "hex": (hexes[0] if hexes else "#dddddd")
                }
                all_colors.append(item)
                color_by_title[str(v.get("title"))] = item

    # Fast lookups
    color_by_id = {c["id"]: c for c in all_colors if c.get("id") is not None}
    title_to_id = {(c["title"] or "").strip().lower(): c["id"] for c in all_colors if c.get("id") is not None}

    # Resolve the specific option IDs for White and Black (case-insensitive)
    white_id = title_to_id.get("white")
    black_id = title_to_id.get("black")
    white_hex = color_by_id.get(white_id, {}).get("hex", "#ffffff") if white_id is not None else "#ffffff"
    black_hex = color_by_id.get(black_id, {}).get("hex", "#000000") if black_id is not None else "#000000"

    # Map variant_id -> color via variant["options"] or title fallback
    def _variant_color_tuple(var: dict) -> tuple[str,str]:
        # returns (title, hex)
        # options can be dict or list
        ctitle = None
        opts = var.get("options")
        if isinstance(opts, dict):
            ctitle = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
        elif isinstance(opts, list):
            for o in opts:
                try:
                    name = (o.get("name") or "").strip().lower()
                    if name in ("color", "colour"):
                        ctitle = o.get("value") or o.get("title")
                        break
                except AttributeError:
                    pass
        if not ctitle:
            t = var.get("title") or ""
            ctitle = t.split(" / ")[0] if " / " in t else None
        ctitle = str(ctitle) if ctitle else None
        hexv = color_by_title.get(ctitle, {}).get("hex", "#dddddd") if ctitle else "#dddddd"
        return (ctitle or "—", hexv)

    def _variant_color_id(var: dict):
        """
        Return the color option *id* for a variant if we can determine it, else None.
        Handles both dict-style and list-style options. Falls back to title -> id mapping.
        """
        opts = var.get("options")
        # dict form: {"color": "Black", ...}
        if isinstance(opts, dict):
            ctitle = (opts.get("color") or opts.get("Color") or
                      opts.get("colour") or opts.get("Colour"))
            if ctitle:
                return title_to_id.get(str(ctitle).strip().lower())
        # list form: [{"name":"Color","value":"Black","id":525}, ...]
        if isinstance(opts, list):
            for o in opts:
                try:
                    name = (o.get("name") or "").strip().lower()
                    if name in ("color", "colour"):
                        # Prefer ID if present
                        if o.get("id") is not None:
                            return o["id"]
                        val = o.get("value") or o.get("title")
                        if val:
                            return title_to_id.get(str(val).strip().lower())
                except AttributeError:
                    continue
        # fallback: parse from `title` like "Black / S"
        t = var.get("title") or ""
        if " / " in t:
            ctitle = t.split(" / ")[0].strip().lower()
            return title_to_id.get(ctitle)
        return None

    for var in (full.get("variants") or []):
        vid = var.get("id")
        if vid is None:
            continue
        cid = _variant_color_id(var)
        if cid is not None and cid in color_by_id:
            cinfo = color_by_id[cid]
            color_by_variant_id[int(vid)] = {"id": cid, "title": cinfo["title"], "hex": cinfo["hex"]}
        else:
            # Fallback to tuple if we can't resolve ID (keeps previous behaviour)
            ctitle, chex = _variant_color_tuple(var)
            color_by_variant_id[int(vid)] = {"id": None, "title": ctitle, "hex": chex}

    # Identify White and Black variant sets
    white_variant_ids = set()
    black_variant_ids = set()
    for var in (full.get("variants") or []):
        vid = var.get("id")
        if vid is None:
            continue
        cid = _variant_color_id(var)
        if cid is None:
            continue
        if white_id is not None and cid == white_id:
            white_variant_ids.add(int(vid))
        if black_id is not None and cid == black_id:
            black_variant_ids.add(int(vid))

    # Compute template colors actually enabled (as before)
    used_titles = set()
    for var in (full.get("variants") or []):
        if var.get("is_enabled", True) is False:
            continue
        used_titles.add(_variant_color_tuple(var)[0])

    template_colors_used = []
    for title in sorted(used_titles, key=lambda s: s.lower()):
        if title in color_by_title:
            template_colors_used.append(color_by_title[title])
        else:
            template_colors_used.append({"id": None, "title": title, "hex": "#dddddd"})

    # Dropdown list: all available colors (alphabetical)
    available_colors = sorted(all_colors, key=lambda c: (c["title"] or "").lower())

    # --- Pick FRONT images specifically for Black and White garments ---
    def _first_front_image_src(placeholders: list[dict]) -> str | None:
        # Return the first image src under position == "front" (or first placeholder if no position markers)
        if not placeholders:
            return None
        # Prefer explicit front
        for ph in placeholders:
            if str(ph.get("position", "")).lower() == "front":
                for img in (ph.get("images") or []):
                    if isinstance(img, dict) and img.get("src"):
                        return img["src"]
        # Fallback: any placeholder with src
        for ph in placeholders:
            for img in (ph.get("images") or []):
                if isinstance(img, dict) and img.get("src"):
                    return img["src"]
        return None

    black_front_src = None
    white_front_src = None

    for pa in (full.get("print_areas") or []):
        vids = set(int(v) for v in (pa.get("variant_ids") or []))
        if not vids:
            continue

        # If this print_area covers any BLACK variants, record its front src
        if (not black_front_src) and (black_variant_ids & vids):
            black_front_src = _first_front_image_src(pa.get("placeholders") or [])

        # If this print_area covers any WHITE variants, record its front src
        if (not white_front_src) and (white_variant_ids & vids):
            white_front_src = _first_front_image_src(pa.get("placeholders") or [])

        # Early exit if both found
        if black_front_src and white_front_src:
            break

    # Panels:
    #  - Light-design panel is for DARK garments → use BLACK front image
    #  - Dark-design panel is for LIGHT garments → use WHITE front image
    light_panel_image = black_front_src
    dark_panel_image = white_front_src

    # Collect ALL colors whose front print-area uses the same image chosen above.
    light_panel_colors = {}  # hex -> title
    dark_panel_colors = {}  # hex -> title

    # --- Build image -> colors mapping for *front* placements only ---
    def _front_src_from_pa(pa: dict) -> str | None:
        # Prefer explicit "front" placeholder
        for ph in (pa.get("placeholders") or []):
            if str(ph.get("position", "")).lower() == "front":
                for img in (ph.get("images") or []):
                    if isinstance(img, dict) and img.get("src"):
                        return img["src"]
        # Fallback: any placeholder with an image
        for ph in (pa.get("placeholders") or []):
            for img in (ph.get("images") or []):
                if isinstance(img, dict) and img.get("src"):
                    return img["src"]
        return None

    # Colors actually present on this product (from enabled variants)
    colors_in_product = set()
    for var in (full.get("variants") or []):
        if var.get("is_enabled", True) is False:
            continue
        cinfo = color_by_variant_id.get(int(var.get("id") or 0))
        if cinfo and cinfo.get("title") and cinfo.get("hex"):
            colors_in_product.add((cinfo["title"], cinfo["hex"]))

    # Map each *front image src* to the set of colors it applies to
    from collections import defaultdict
    image_to_colors: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for pa in (full.get("print_areas") or []):
        pa_src = _front_src_from_pa(pa)
        if not pa_src:
            continue
        vids = [int(v) for v in (pa.get("variant_ids") or [])]
        for v in vids:
            cinfo = color_by_variant_id.get(v)
            if cinfo and cinfo.get("title") and cinfo.get("hex"):
                image_to_colors[pa_src].add((cinfo["title"], cinfo["hex"]))

    # Identify a "default" image if one covers many colors
    DEFAULT_THRESHOLD = 10
    total_colors = len(colors_in_product)
    default_src = None
    if image_to_colors:
        # pick the image with max coverage
        src_max = max(image_to_colors.keys(), key=lambda s: len(image_to_colors[s]))
        max_count = len(image_to_colors[src_max])
        # Heuristic: treat as default if it's large (>=10) or a clear majority (>=50%)
        if max_count >= DEFAULT_THRESHOLD or (total_colors > 0 and max_count >= total_colors * 0.5):
            default_src = src_max

    # Helper to turn a set of (title,hex) into {hex:title} sorted by title
    def _to_dict_hex_title(s: set[tuple[str, str]]) -> dict[str, str]:
        return {h: t for t, h in sorted(s, key=lambda th: th[0].lower())}

    # Build panel color sets:
    #  - If the panel shows the default image: show product colors MINUS all unique sets
    #  - If the panel shows a non-default image: show ONLY that image's explicit set
    light_panel_colors = {}
    dark_panel_colors = {}

    # Union of all non-default image color sets
    non_default_union: set[tuple[str, str]] = set()
    if default_src:
        for src, s in image_to_colors.items():
            if src == default_src:
                continue
            non_default_union |= s

    if light_panel_image:
        if default_src and light_panel_image == default_src:
            # default image on this panel -> everything else
            panel_set = colors_in_product - non_default_union
        else:
            panel_set = image_to_colors.get(light_panel_image, set())
        light_panel_colors = _to_dict_hex_title(panel_set)

    if dark_panel_image:
        if default_src and dark_panel_image == default_src:
            panel_set = colors_in_product - non_default_union
        else:
            panel_set = image_to_colors.get(dark_panel_image, set())
        dark_panel_colors = _to_dict_hex_title(panel_set)

    # Fallback: if a panel still has nothing but we *did* detect White/Black by ID, seed those so UI isn't empty
    if not light_panel_colors and black_id is not None:
        light_panel_colors = {black_hex: "Black"}
    if not dark_panel_colors and white_id is not None:
        dark_panel_colors = {white_hex: "White"}
    # Saved design URLs if present locally
    from pathlib import Path
    base = Path("data/designs") / str(product_id)
    def _first_url(which):
        for pth in base.glob(f"{which}.*"):
            if pth.suffix.lower() in {".png",".jpg",".jpeg",".webp"}:
                return f"/designs/{product_id}/{which}"
        return None

    ctx = {
        "id": str(full.get("id") or full.get("_id") or product_id),
        "title": full.get("title") or full.get("name") or "",
        "description": full.get("description") or "",
        "raw": full,

        "template_colors_used": template_colors_used,
        "available_colors": available_colors,  # dropdown list, alphabetical

        # local uploads (override previews if present)
        "light_url": _first_url("light") or light_panel_image,
        "dark_url":  _first_url("dark")  or dark_panel_image,

        # initial saved pills per panel (from existing product mapping)
        "initial_saved_light": [{"title": t, "hex": h} for h,t in sorted(light_panel_colors.items(), key=lambda kv: kv[1].lower())],
        "initial_saved_dark":  [{"title": t, "hex": h} for h,t in sorted(dark_panel_colors.items(),  key=lambda kv: kv[1].lower())],
    }
    return render_template("printify_edit.html", p=ctx)



# -----------------------------
# Designs — CRUD with JSON files
# -----------------------------
@app.get("/api/designs")
def list_designs():
    return jsonify(store.list("designs"))


@app.post("/api/designs")
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


@app.get("/api/designs/<slug>")
def get_design(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    return jsonify(design)


@app.patch("/api/designs/<slug>")
def update_design(slug):
    updates = request.json or {}
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404
    design.update(updates)
    store.upsert("designs", slug, design)
    return jsonify(design)


# -----------------------------
# AI: Titles/Descriptions/Keywords & Color Suggestions
# -----------------------------
@app.post("/api/designs/<slug>/ai/metadata")
def ai_metadata(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    local_docs = {
        "personas_pdf": str(BASE_DIR / "KD Personas.pdf"),
        "principles": str(BASE_DIR / "T-Shirt Design Core Principals.txt"),
        "policies": str(BASE_DIR / "policies.md"),
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


@app.post("/api/designs/<slug>/ai/colors")
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
# Mockup generation (flat-lay composites via Pillow)
# -----------------------------
@app.post("/api/designs/<slug>/mockups")
def generate_mockups(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    config = request.json or {}
    out_paths = generate_mockups_for_design(
        design_png_path=design["design_png_path"],
        templates=config.get("templates", []),
        placements=config.get("placements", {}),
        out_dir=MOCKUPS_DIR / design["slug"],
        scale=config.get("scale", 1.0),
    )
    design["status"]["mockups_generated"] = True
    design.setdefault("assets", {})["mockups"] = [str(p) for p in out_paths]
    store.upsert("designs", slug, design)
    return jsonify({"mockups": [str(p) for p in out_paths]})


# -----------------------------
# Printify: create product & publish to Shopify (via Printify)
# -----------------------------
@app.post("/api/designs/<slug>/printify/create-product")
def printify_create_product(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    created = printify.create_product(
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


@app.post("/api/designs/<slug>/printify/publish")
def printify_publish(slug):
    design = store.get("designs", slug)
    if not design:
        return jsonify({"error": "Not found"}), 404

    payload = request.json or {}
    result = printify.publish_to_shopify(
        product_id=payload.get("product_id"),
        publish_details=payload.get("publish_details", {}),
    )
    design["status"]["published_shopify"] = True
    store.upsert("designs", slug, design)
    return jsonify(result)


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@app.post("/api/shopify/products/<product_id>/images")
def shopify_upload_images(product_id):
    payload = request.json or {}
    image_paths = payload.get("image_paths", [])
    uploaded = shopify.upload_product_images(product_id, image_paths)
    return jsonify({"uploaded": uploaded})


@app.get("/api/products")
def api_list_products():
    return jsonify(store.list(PRODUCTS_COLLECTION))


@app.get("/api/printify/products")
def api_list_printify_products():
    return jsonify(store.list(PRINTIFY_PRODUCTS_COLLECTION))


@app.get("/api/printify/colors/<product_id>")
def api_printify_colors(product_id):
    """Return the distinct color names (and codes if present) available for this product’s blueprint/provider."""
    prod = printify.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if bp is None or pp is None:
        return jsonify({"error": "blueprint_id or print_provider_id missing on product"}), 400

    try:
        cat = printify.get_blueprint_provider_variants(bp, pp)
    except Exception as e:
        # If this provider doesn't serve this blueprint, show what's available
        try:
            provs = printify.list_blueprint_providers(bp)
        except Exception:
            provs = {}
        return jsonify({
            "error": f"Provider {pp} not found for blueprint {bp}.",
            "hint": "Use one of the providers listed for this blueprint.",
            "available_providers": provs
        }), 404

    # v1 endpoint returns a list or an object with 'variants'
    variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
    colors = set()
    color_map = {}  # color -> list of variant_ids
    for v in variants:
        opts = v.get("options") or {}
        color = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
        if color:
            colors.add(color)
            vid = v.get("id")
            if vid is not None:
                color_map.setdefault(color, []).append(vid)

    return jsonify({
        "blueprint_id": int(bp),
        "print_provider_id": int(pp),
        "colors": sorted(colors),
        "color_variants": color_map
    })


@app.post("/api/printify/templates/<product_id>/extract_colors")
def api_printify_extract_colors(product_id: str):
    """
    Load a template product from Printify and persist its color set (with hex)
    to assets/mockups/{blueprint_id}_{print_provider_id}/colors.json
    """
    try:
        from services.printify import PrintifyClient
        pci = PrintifyClient()
        tpl = pci.get_product(product_id)
    except Exception as e:
        return jsonify({"error": f"Failed to load product {product_id}", "detail": str(e)}), 400

    bp = tpl.get("blueprint_id")
    pp = tpl.get("print_provider_id")
    options = tpl.get("options") or []

    color_option = None
    for opt in options:
        # Printify uses type: "color" and often name: "Colors"
        if (opt.get("type") == "color") or (opt.get("name", "").lower() == "colors"):
            color_option = opt
            break

    if not color_option:
        return jsonify({"error": "No color option found on template product."}), 404

    values = color_option.get("values") or []

    # Normalize to a compact list of {id, title, hex}
    colors_out = []
    for v in values:
        hexes = v.get("colors") or []
        # Some entries may provide multiple hexes (very rare) – keep the first for UI,
        # but store all in 'hexes' as well.
        primary_hex = (hexes[0] if hexes else None)
        colors_out.append({
            "id": v.get("id"),
            "title": v.get("title"),
            "hex": primary_hex,
            "hexes": hexes
        })

    # Compose write path
    folder_name = f"{bp}_{pp}"
    folder = MOCKUPS_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / "colors.json"

    payload = {
        "blueprint_id": bp,
        "print_provider_id": pp,
        "option_name": color_option.get("name") or "Colors",
        "values": colors_out,
        "generated_from_product_id": tpl.get("id"),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }

    # Save
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return jsonify({
        "ok": True,
        "message": "Colors extracted.",
        "path": str(out_path.relative_to(BASE_DIR)),
        "count": len(colors_out)
    })


@app.post("/api/designs/upload")
def upload_designs():
    """
    Multipart form-data:
      - product_id (text)
      - light (file) optional
      - dark (file) optional
    Saves to data/designs/<product_id>/{light|dark}<ext>
    """
    product_id = request.form.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    dest = Path("data/designs") / str(product_id)
    dest.mkdir(parents=True, exist_ok=True)

    saved = {}
    for key in ("light", "dark"):
        file = request.files.get(key)
        if not file or not file.filename:
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTS:
            return jsonify({"error": f"{key} must be one of {ALLOWED_EXTS}"}), 400
        out = dest / f"{key}{ext}"
        file.save(out)
        saved[key] = str(out)

    return jsonify({"ok": True, "saved": saved})



@app.post("/api/printify/products/duplicate")
def api_printify_duplicate():
    data = request.get_json(force=True) or {}
    product_id = data.get("product_id") or data.get("template_id")
    new_title = data.get("title") or "New Product"
    new_description = data.get("description") or ""

    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    pci = PrintifyClient()

    # 1) Fetch the template product
    template = pci.get_product(product_id=product_id)

    # 2) Create a new product using the “lean” payload (no SKUs, slim variants & print_areas)
    created = pci.duplicate_from_template(
        template=template,
        title=new_title,
        description=new_description,
        tags=template.get("tags", [])
    )

    return jsonify({"ok": True, "created": created}), 201


@app.post("/api/products/cache/update")
def update_products_cache():
    # Fetch all products from Shopify and normalize to our schema
    raw_products = shopify.list_all_products(limit=250)
    normalized = {}

    for p in raw_products:
        pid = str(p.get("id"))
        handle = p.get("handle")
        url = shopify.product_url(handle)
        product_type = p.get("product_type") or ""
        desc = p.get("body_html") or ""
        tags = [t.strip() for t in (p.get("tags") or "").split(",") if t.strip()]
        status = p.get("status") or "unknown"
        created_at = p.get("created_at")
        updated_at = p.get("updated_at")

        # Primary image
        img = None
        if p.get("image") and p["image"].get("src"):
            img = p["image"]["src"]
        elif p.get("images"):
            img = p["images"][0].get("src") if p["images"] else None

        # Map image_id -> src for variant thumbnails
        images_map = {}
        for _im in (p.get("images") or []):
            if _im.get("id") and _im.get("src"):
                images_map[_im["id"]] = _im["src"]

        # Variants (color/size detection via option names)
        option_map = {opt.get("position"): opt.get("name", "") for opt in (p.get("options") or [])}
        color_pos = next((pos for pos, name in option_map.items() if name and name.lower() in ["color", "colour"]),
                         None)
        size_pos = next((pos for pos, name in option_map.items() if name and name.lower() == "size"), None)

        variants = []
        # track first thumbnail per color
        color_image_map = {}

        for v in (p.get("variants") or []):
            opts = [v.get(f"option{i}") for i in range(1, 4)]
            color = opts[(color_pos - 1)] if color_pos else None
            size = opts[(size_pos - 1)] if size_pos else None

            # pick image for this variant if available
            v_image_src = None
            v_image_id = v.get("image_id")
            if v_image_id and v_image_id in images_map:
                v_image_src = images_map[v_image_id]
            else:
                # Some payloads embed a direct URL or dict
                vimg = v.get("image") or v.get("preview")
                if isinstance(vimg, dict):
                    v_image_src = vimg.get("src") or vimg.get("url")
                elif isinstance(vimg, str):
                    v_image_src = vimg

            # remember the first image we see for each color (fallback later to primary)
            if color and color not in color_image_map:
                color_image_map[color] = v_image_src  # may be None for now

            variants.append({
                "id": v.get("id"),
                "title": v.get("title"),
                "sku": v.get("sku"),
                "color": color,
                "size": size,
                "price": v.get("price"),
                "available": v.get("available", True),
                "image": v_image_src,
            })

        # Build compact color variants list using first image per color (fallback to primary)
        color_variants = []
        seen = set()
        for color, cimg in color_image_map.items():
            if not color:
                continue
            color_variants.append({
                "color": color,
                "image": cimg or img  # fallback to product primary image
            })
            seen.add(color)

        # ensure we capture colors that had no image map but exist in variants
        for v in variants:
            c = v.get("color")
            if c and c not in seen:
                color_variants.append({"color": c, "image": v.get("image") or img})
                seen.add(c)

        normalized[pid] = {
            "id": pid,
            "title": p.get("title"),
            "url": url,
            "type": product_type,
            "description": desc,
            "tags": tags,
            "variants": variants,  # full variants preserved
            "color_variants": color_variants,  # new compact list for UI
            "primary_image": img,
            "handle": handle,
            "status": status,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    # Save cache
    store.replace_collection(PRODUCTS_COLLECTION, normalized)
    return jsonify({"count": len(normalized)})


@app.post("/api/printify/products/cache/update")
def update_printify_products_cache():
    """Download Printify products, normalize, and store cache."""
    shop_id = (request.json or {}).get("shop_id") or os.getenv("PRINTIFY_SHOP_ID")
    if not shop_id:
        return jsonify({"error": "Missing shop_id (provide in body or set PRINTIFY_SHOP_ID)"}), 400

    normalized = {}
    page = 1
    total = 0

    while True:
        page_data = printify.list_products(page=page, limit=100)
        # API may return either {"data":[...], "last_page":N, ...} or {"products":[...]}
        data_list = page_data.get("data") or page_data.get("products") or []
        if not data_list:
            break

        for p in data_list:
            pid = str(p.get("id") or p.get("_id") or "")
            if not pid:
                continue

            title = p.get("title") or p.get("name") or ""
            # main image: tolerate dicts or plain strings; fallback to preview
            primary_image = None
            imgs = p.get("images") or []
            if imgs:
                first = imgs[0]
                if isinstance(first, dict):
                    primary_image = first.get("src") or first.get("url")
                elif isinstance(first, str):
                    primary_image = first
            if not primary_image:
                prv = p.get("preview")
                if isinstance(prv, dict):
                    primary_image = prv.get("src") or prv.get("url")
                elif isinstance(prv, str):
                    primary_image = prv

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            # tolerate dict or string shapes
            if isinstance(ext, dict):
                ext_handle = ext.get("handle") or ext.get("shopify_handle") or ext.get("product_handle")
            elif isinstance(ext, str):
                # in rare cases 'external' may just be the handle
                ext_handle = ext.strip()
            else:
                ext_handle = None

            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"

            # Publication status: simply whether we have a Shopify link
            published = bool(shopify_url)

            # Channel-specific shapes: list, dict, or strings
            if published is None:
                published = False
                sc_props = p.get("sales_channel_properties") or p.get("sales_channels") or []
                # If it's a dict, check values; if list, iterate items; if string, look for 'published'
                if isinstance(sc_props, dict):
                    for v in sc_props.values():
                        if (isinstance(v, dict) and _to_bool(v.get("published"))) or \
                                (isinstance(v, str) and "published" in v.lower()):
                            published = True
                            break
                elif isinstance(sc_props, list):
                    for sc in sc_props:
                        if isinstance(sc, dict):
                            if _to_bool(sc.get("published")):
                                published = True
                                break
                        elif isinstance(sc, str):
                            if "published" in sc.lower():
                                published = True
                                break
                elif isinstance(sc_props, str):
                    published = "published" in sc_props.lower()

            # Try to link to Shopify product URL if Printify gives external handle/id
            shopify_url = None
            ext = p.get("external") or {}
            ext_handle = ext.get("handle")
            if ext_handle:
                shopify_url = f"https://{os.getenv('SHOPIFY_STORE_DOMAIN')}/products/{ext_handle}"
            else:
                # sometimes external id exists; if you store a mapping later, plug it here
                pass

            normalized[pid] = {
                "id": pid,
                "title": title,
                "primary_image": primary_image,
                "published": bool(published),
                "shopify_url": shopify_url,
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            total += 1

        # pagination end?
        last_page = page_data.get("last_page")
        current_page = page_data.get("current_page") or page
        if last_page and current_page < last_page:
            page += 1
            continue
        # Fallback: if no last_page info, stop after first page unless data == limit
        if not last_page and len(data_list) >= 100:
            page += 1
            continue
        break

    store.replace_collection(PRINTIFY_PRODUCTS_COLLECTION, normalized)
    return jsonify({"count": len(normalized)})


@app.post("/api/recommend/colors")
def recommend_colors():
    """
    JSON body:
      { "product_id": "..." }
    Backend will derive blueprint_id & print_provider_id and collect color names + hex.
    Uses any /data/designs/<product_id>/light|dark.* files if present.
    """

    body = request.get_json(force=True) or {}
    product_id = body.get("product_id")
    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    # 1) Load product to get bp/pp and inline colors, if present
    prod = printify.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if not bp or not pp:
        return jsonify({"error":"Product missing blueprint_id or print_provider_id"}), 400

    # 2) Try colors.json first
    from pathlib import Path as _P
    folder = ASSETS_DIR / f"{bp}_{pp}"
    colors_json = folder / "colors.json"
    colors = []
    if colors_json.exists():
        try:
            data = json.loads(colors_json.read_text(encoding="utf-8"))
            for v in (data.get("values") or []):
                colors.append({"id": v.get("id"), "title": v.get("title"), "hex": (v.get("hex") or "#dddddd")})
        except Exception:
            pass

    # 3) If empty, fall back to product.options[type=color].values[].colors[]
    if not colors:
        for opt in (prod.get("options") or []):
            if (opt.get("type") == "color") or (opt.get("name","").lower() == "colors"):
                for v in (opt.get("values") or []):
                    hexes = v.get("colors") or []
                    colors.append({"id": v.get("id"), "title": v.get("title"), "hex": (hexes[0] if hexes else None) or "#dddddd"})

    # 4) If still empty, final fallback to variants endpoint (no hex there, so use placeholders)
    if not colors:
        cat = printify.get_blueprint_provider_variants(bp, pp)
        variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
        # dedupe by color name, fabricate hex = '#dddddd'
        seen = set()
        for v in variants:
            name = (v.get("options") or {}).get("color") or (v.get("options") or {}).get("Color")
            if name and name not in seen:
                colors.append({"id": None, "title": name, "hex": "#dddddd"})
                seen.add(name)

    if not colors:
        return jsonify({"error":"Could not determine provider colors for this product"}), 400

    # 5) Load local design images if present
    base = Path("data/designs") / str(product_id)
    ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    light_file = next((p for p in base.glob("light.*") if p.suffix.lower() in ALLOWED_EXTS), None)
    dark_file  = next((p for p in base.glob("dark.*") if p.suffix.lower() in ALLOWED_EXTS), None)

    def as_image_part(path: Path):
        if not path: return None
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return {"type":"image_url","image_url":{"url": f"data:{mime};base64,{b64}"}}

    # 6) Build the GPT prompt with names + hex
    color_payload = [{"title": c["title"], "hex": c["hex"]} for c in colors]
    messages = [
        {"role":"system","content":"You are a product designer choosing garment colors that best complement a design image for DTG/DTF printing. Consider contrast, readability, and color harmony."},
        {"role":"user","content":[
            {"type":"text","text":(
                "Given the provided design images (light and/or dark versions) and this list of available shirt colors "
                "(each with hex), recommend up to 6 shirt colors total: 3 best for the LIGHT-design (if provided) and "
                "3 best for the DARK-design (if provided). Only choose from the provided colors. "
                "Return strict JSON with keys 'light' and 'dark', each an array like [{\"title\":\"Black\",\"hex\":\"#000000\"}, ...]."
                f"\n\nAvailable colors:\n{json.dumps(color_payload, ensure_ascii=False)}"
            )},
        ] + ([as_image_part(light_file)] if light_file else []) + ([as_image_part(dark_file)] if dark_file else [])}
    ]

    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    if not openai_api_key:
        return jsonify({"error":"OPENAI_API_KEY missing in .env"}), 400

    # 7) Call OpenAI and sanitize results to the available set
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        # normalize + filter to available titles
        avail_by_title = {c["title"]: c for c in color_payload}
        def _filter(side):
            out = []
            for c in (data.get(side) or []):
                t = c.get("title")
                if t in avail_by_title:
                    # keep our canonical hex (ignore model's hex just in case)
                    out.append({"title": t, "hex": avail_by_title[t]["hex"]})
                if len(out) >= 3: break
            return out
        return jsonify({"light": _filter("light"), "dark": _filter("dark"), "available_colors": color_payload})
    except Exception as e:
        return jsonify({"error": str(e)}), 400



# -----------------------------
# Static serve for generated mockups (for quick preview)
# -----------------------------
@app.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = MOCKUPS_DIR
    return send_from_directory(dirpath, filename)


if __name__ == "__main__":
    app.run(debug=True)
