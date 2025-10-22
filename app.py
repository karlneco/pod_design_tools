import os
import json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv

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
printify = PrintifyClient(os.getenv("PRINTIFY_API_TOKEN"))
shopify = ShopifyClient(
    store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"),
    admin_token=os.getenv("SHOPIFY_ADMIN_TOKEN"),
    api_version=os.getenv("SHOPIFY_API_VERSION", "2024-10"),
)

PRODUCTS_COLLECTION = "shopify_products"


# -----------------------------
# Simple Pages
# -----------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/products")
def products_page():
    products = store.list(PRODUCTS_COLLECTION)
    return render_template("products.html", products=products, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


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
        shop_id=payload.get("shop_id"),
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
        shop_id=payload.get("shop_id"),
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

        # Primary image
        img = None
        if p.get("image") and p["image"].get("src"):
            img = p["image"]["src"]
        elif p.get("images"):
            img = p["images"][0].get("src") if p["images"] else None

        # Variants (color/size detection via option names)
        option_map = {opt.get("position"): opt.get("name", "") for opt in (p.get("options") or [])}
        color_pos = next((pos for pos, name in option_map.items() if name and name.lower() in ["color", "colour"]),
                         None)
        size_pos = next((pos for pos, name in option_map.items() if name and name.lower() == "size"), None)

        variants = []
        for v in (p.get("variants") or []):
            opts = [v.get(f"option{i}") for i in range(1, 4)]
            color = opts[(color_pos - 1)] if color_pos else None
            size = opts[(size_pos - 1)] if size_pos else None
            variants.append({
                "id": v.get("id"),
                "title": v.get("title"),
                "sku": v.get("sku"),
                "color": color,
                "size": size,
                "price": v.get("price"),
                "available": v.get("available", True),
            })

        normalized[pid] = {
            "id": pid,
            "title": p.get("title"),
            "url": url,
            "type": product_type,
            "description": desc,
            "tags": tags,
            "variants": variants,
            "primary_image": img,
            "handle": handle,
            "updated_at": p.get("updated_at"),
        }

    # Save cache
    store.replace_collection(PRODUCTS_COLLECTION, normalized)
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
