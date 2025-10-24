import os
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime

from storage import JsonStore
from services.printify import PrintifyClient
from services.shopify import ShopifyClient
from services.openai_svc import suggest_metadata, suggest_colors
from mockups.composer import generate_mockups_for_design

load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
ASSETS_DIR = BASE_DIR / "assets"
MOCKUPS_DIR = BASE_DIR / "generated_mockups"

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


@app.get("/printify/edit/<product_id>")
def printify_edit(product_id):
    shop_id = os.getenv("PRINTIFY_SHOP_ID")
    if not shop_id:
        return "Missing PRINTIFY_SHOP_ID", 400
    try:
        full = printify.get_product(product_id)
    except Exception as e:
        return f"Failed to load product: {e}", 400

    # Log the raw JSON to your Flask log for easy comparison
    try:
        import json as _json
        app.logger.info("Printify product %s:\n%s", product_id, _json.dumps(full, indent=2, ensure_ascii=False))
    except Exception:
        pass

    # Minimal fields for now
    ctx = {
        "id": str(full.get("id") or full.get("_id") or product_id),
        "title": full.get("title") or full.get("name") or "",
        "description": full.get("description") or "",
        "raw": full,  # pass raw object to template for on-page dump
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


# -----------------------------
# Static serve for generated mockups (for quick preview)
# -----------------------------
@app.get("/mockups/<path:filename>")
def serve_mockup(filename):
    dirpath = MOCKUPS_DIR
    return send_from_directory(dirpath, filename)


if __name__ == "__main__":
    app.run(debug=True)
