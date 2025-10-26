import base64
import json
import mimetypes
import os
from pathlib import Path

from flask import Blueprint, request, jsonify, send_from_directory
from openai import OpenAI

from .. import Config
from ..extensions import store, shopify_client, printify_client

bp = Blueprint("api", __name__)

PRODUCTS_COLLECTION = "shopify_products"


@bp.get("/products")
def api_list_products():
    return jsonify(store.list(PRODUCTS_COLLECTION))


@bp.post("/products/cache/update")
def update_products_cache():
    # Fetch all products from Shopify and normalize to our schema
    raw_products = shopify_client.list_all_products(limit=250)
    normalized = {}

    for p in raw_products:
        pid = str(p.get("id"))
        handle = p.get("handle")
        url = shopify_client.product_url(handle)
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

@bp.post("/recommend/colors")
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
    prod = printify_client.get_product(product_id)
    bp = prod.get("blueprint_id")
    pp = prod.get("print_provider_id")
    if not bp or not pp:
        return jsonify({"error": "Product missing blueprint_id or print_provider_id"}), 400

    # 2) Try colors.json first
    from pathlib import Path as _P
    folder = Config.ASSETS_DIR / f"{bp}_{pp}"
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
            if (opt.get("type") == "color") or (opt.get("name", "").lower() == "colors"):
                for v in (opt.get("values") or []):
                    hexes = v.get("colors") or []
                    colors.append(
                        {"id": v.get("id"), "title": v.get("title"), "hex": (hexes[0] if hexes else None) or "#dddddd"})

    # 4) If still empty, final fallback to variants endpoint (no hex there, so use placeholders)
    if not colors:
        cat = printify_client.get_blueprint_provider_variants(bp, pp)
        variants = cat if isinstance(cat, list) else (cat.get("variants") or [])
        # dedupe by color name, fabricate hex = '#dddddd'
        seen = set()
        for v in variants:
            name = (v.get("options") or {}).get("color") or (v.get("options") or {}).get("Color")
            if name and name not in seen:
                colors.append({"id": None, "title": name, "hex": "#dddddd"})
                seen.add(name)

    if not colors:
        return jsonify({"error": "Could not determine provider colors for this product"}), 400

    # 5) Load local design images if present
    base = Path("data/designs") / str(product_id)
    ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    light_file = next((p for p in base.glob("light.*") if p.suffix.lower() in ALLOWED_EXTS), None)
    dark_file = next((p for p in base.glob("dark.*") if p.suffix.lower() in ALLOWED_EXTS), None)

    def as_image_part(path: Path):
        if not path: return None
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}

    # 6) Build the GPT prompt with names + hex
    color_payload = [{"title": c["title"], "hex": c["hex"]} for c in colors]
    messages = [
        {"role": "system",
         "content": "You are a product designer choosing garment colors that best complement a design image for DTG/DTF printing. Consider contrast, readability, and color harmony."},
        {"role": "user", "content": [
                                        {"type": "text", "text": (
                                            "Given the provided design images (light and/or dark versions) and this list of available shirt colors "
                                            "(each with hex), recommend up to 6 shirt colors total: 3 best for the LIGHT-design (if provided) and "
                                            "3 best for the DARK-design (if provided). Only choose from the provided colors. "
                                            "Return strict JSON with keys 'light' and 'dark', each an array like [{\"title\":\"Black\",\"hex\":\"#000000\"}, ...]."
                                            f"\n\nAvailable colors:\n{json.dumps(color_payload, ensure_ascii=False)}"
                                        )},
                                    ] + ([as_image_part(light_file)] if light_file else []) + (
                                        [as_image_part(dark_file)] if dark_file else [])}
    ]

    openai_api_key = os.getenv("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    if not openai_api_key:
        return jsonify({"error": "OPENAI_API_KEY missing in .env"}), 400

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
