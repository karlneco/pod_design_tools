import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

from .. import config, Config
from ..extensions import store, printify_client as printify

bp = Blueprint("printify_api", __name__)
PRINTIFY_PRODUCTS_COLLECTION = "printify_products"


def _to_bool(param):
    if isinstance(param, bool):
        return param
    if isinstance(param, (int, float)):
        return param != 0
    if isinstance(param, str):
        return param.strip().lower() in ("1", "true", "yes", "y", "on")
    return False


def _normalize_printify_for_cache(p: dict) -> dict:
    """
    Normalize a Printify product into the minimal shape we store in
    PRINTIFY_PRODUCTS_COLLECTION. Also extracts Shopify product id
    from product['external'] and keeps it.
    """
    pid = str(p.get("id") or p.get("_id") or "")

    # Title
    title = p.get("title") or p.get("name") or ""

    # Primary image (same logic you already had)
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

    # External / Shopify bits
    ext = p.get("external") or {}
    shopify_product_id = None
    shopify_handle = None

    if isinstance(ext, dict):
        # Common shapes in Printify
        shopify_product_id = ext.get("id") or ext.get("product_id")
        shopify_handle = (
            ext.get("handle")
            or ext.get("shopify_handle")
            or ext.get("product_handle")
        )
    elif isinstance(ext, str):
        # Sometimes 'external' is just the id or the handle
        if ext.isdigit():
            shopify_product_id = ext
        else:
            shopify_handle = ext.strip()

    # Normalize Shopify domain from env
    shop_domain = (os.getenv("SHOPIFY_STORE_DOMAIN") or "").strip()
    if shop_domain.startswith("http://") or shop_domain.startswith("https://"):
        shop_domain = shop_domain.split("://", 1)[1]
    shop_domain = shop_domain.strip("/")

    shopify_url = None
    if shop_domain and shopify_handle:
        shopify_url = f"https://{shop_domain}/products/{shopify_handle}"

    published = bool(shopify_url)

    return {
        "id": pid,
        "title": title,
        "primary_image": primary_image,
        "published": published,
        "shopify_url": shopify_url,
        "shopify_product_id": str(shopify_product_id) if shopify_product_id else None,
        "shopify_handle": shopify_handle,
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
    }



@bp.get("/printify/colors/<product_id>")
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


@bp.get("/printify/products")
def api_list_printify_products():
    return jsonify(store.list(PRINTIFY_PRODUCTS_COLLECTION))


@bp.post("/printify/products/cache/update")
def update_printify_products_cache():
    """Download Printify products, normalize, and store cache."""
    shop_id = (request.json or {}).get("shop_id") or os.getenv("PRINTIFY_SHOP_ID")
    if not shop_id:
        return jsonify({"error": "Missing shop_id (provide in body or set PRINTIFY_SHOP_ID)"}), 400

    normalized = {}
    page = 1

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
            normalized[pid] = _normalize_printify_for_cache(p)

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


@bp.post("/printify/products/duplicate")
def api_printify_duplicate():
    data = request.get_json(force=True) or {}
    product_id = data.get("product_id") or data.get("template_id")
    new_title = data.get("title") or "New Product"
    new_description = data.get("description") or ""

    if not product_id:
        return jsonify({"error": "product_id is required"}), 400

    # 1) Fetch the template product
    template = printify.get_product(product_id=product_id)

    # 2) Create a new product using the “lean” payload (no SKUs, slim variants & print_areas)
    created = printify.duplicate_from_template(
        template=template,
        title=new_title,
        description=new_description,
        tags=template.get("tags", [])
    )

    return jsonify({"ok": True, "created": created}), 201


@bp.post("/printify/templates/<product_id>/extract_colors")
def api_printify_extract_colors(product_id: str):
    """
    Load a template product from Printify and persist its color set (with hex)
    to assets/mockups/{blueprint_id}_{print_provider_id}/colors.json
    """
    try:
        tpl = printify.get_product(product_id)
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
    folder = Config.MOCKUPS_DIR / folder_name
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
        "path": str(out_path.relative_to(Config.BASE_DIR)),
        "count": len(colors_out)
    })


@bp.post("/printify/products/<product_id>/apply_design")
def apply_design(product_id):
    """
    Form-data or JSON:
      - which: "light"|"dark" (required) - can be query param, form field, or JSON
      - file: (optional) image file to upload
      - use_saved: "1"|"true" (optional) to use /data/designs/<product_id>/<which>.* if no file provided
    Returns: { image_id, src, product }
    """
    # Try to get 'which' from: query args > form data > JSON body
    which = (request.args.get("which") or
             request.form.get("which") or
             (request.json.get("which") if request.is_json else None))
    if which not in ("light", "dark"):
        return jsonify({"error": "which must be 'light' or 'dark'"}), 400

    # Determine source image
    upload_file = request.files.get("file")
    # Try to get 'use_saved' from: query args > form data > JSON body
    use_saved_param = (request.args.get("use_saved") or
                       request.form.get("use_saved") or
                       (request.json.get("use_saved") if request.is_json else None))
    use_saved = str(use_saved_param or "").lower() in ("1", "true") if upload_file is None else False
    local_path = None

    if upload_file is None and use_saved:
        base = Path("data/designs") / str(product_id)
        for p in base.glob(f"{which}.*"):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                local_path = str(p)
                break
        if not local_path:
            return jsonify({"error": "No saved design file found"}), 404

    # Upload to Printify
    if upload_file is not None:
        # Save temp
        tmp = Path("data/tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp / upload_file.filename
        upload_file.save(tmp_path)
        up = printify.upload_image_file(file_path=str(tmp_path), file_name=upload_file.filename)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        up = printify.upload_image_file(file_path=local_path, file_name=Path(local_path).name)

    image_id = up.get("id")
    if not image_id:
        return jsonify({"error": "Upload to Printify failed", "detail": up}), 400

    # Load product then set FRONT placeholder to this image
    prod = printify.get_product(product_id)
    patch = printify.ensure_front_with_image(prod, image_id=image_id, x=0.5, y=0.5, scale=1.0, angle=0)
    printify.update_product(product_id, patch)

    # Re-fetch to get resolved mockup src (Printify echoes image entries with 'src')
    updated = printify.get_product(product_id)

    # Try to find an 'src' for our image_id on front
    src = None
    for pa in (updated.get("print_areas") or []):
        for ph in (pa.get("placeholders") or []):
            if (ph.get("position") or "").lower() != "front":
                continue
            for img in (ph.get("images") or []):
                if str(img.get("id")) == str(image_id):
                    src = img.get("src") or img.get("url")
                    break

    return jsonify({"image_id": image_id, "src": src, "product": updated})


@bp.post("/printify/ai/generate_metadata")
def ai_generate_metadata():
    """
    Body:
    {
      "product_id": "...",
      "images": ["data:image/jpeg;base64,...", "/designs/...png", "https://..."],
      "title_hint": "...",
      "collections": [...],
      "colors": [{"title":"Black","hex":"#000000"}, ...]
    }
    """
    from openai import OpenAI
    import base64, mimetypes, re

    body = request.get_json(force=True)
    pid = body.get("product_id")
    title_hint = body.get("title_hint", "")
    colors = body.get("colors", [])
    incoming_images = body.get("images", []) or []

    # Prefer explicitly provided images array.
    incoming_images = body.get("images", []) or []

    # Filter out the default logo by id if it sneaks in as a Printify URL with that id (we'll also filter later).
    def is_default_logo_url(u: str) -> bool:
        # heuristic: if URL contains the known default id; adjust if your CDN path differs
        return isinstance(u, str) and Config.DEFAULT_FRONT_IMAGE_ID in u

    DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,", re.IGNORECASE)

    def as_image_part(url_or_data: str | None):
        """
        Accept:
          - data URLs (already jpeg/png/webp)
          - http(s) URLs
          - /designs/... (local path we can embed)
        Return an OpenAI image part or None.
        """
        if not url_or_data:
            return None
        try:
            s = str(url_or_data)
            # data URL (already encoded) -> pass through
            if DATA_URL_RE.match(s):
                return {"type": "image_url", "image_url": {"url": s}}

            # remote URL
            if s.startswith("http://") or s.startswith("https://"):
                return {"type": "image_url", "image_url": {"url": s}}

            # local saved design
            if s.startswith("/designs/"):
                from pathlib import Path
                file_path = Path("." + s)
                if not file_path.exists():
                    return None
                b64 = base64.b64encode(file_path.read_bytes()).decode("utf-8")
                mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
                return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        except Exception:
            pass
        return None

    imgs = [as_image_part(x) for x in incoming_images]
    imgs = [x for x in imgs if x]

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt_text = f"""
    You are a creative copywriter for a Japanese travel-inspired apparel brand.
    You will be given 0–2 design images. If images are present, you MUST ground your copy
    in what you SEE: motifs, vibe, era, attitude, palette, mood. Do NOT literally describe
    small details or list elements; write a short emotional hook.

    Write HTML with this structure:
    - <h2>Headline</h2> — 2–6 evocative words (not just the product name)
    - <p class="p4">Body</p> — 2–4 sentences max. Focus on vibe, place, feeling.
      Use <span class="s2"><b>...</b></span> to highlight 1–3 key terms. Add 2–3 fitting emojis.
      Do NOT restate the artwork literally; no “this design shows …”.

    Return STRICT JSON ONLY:
    {{
      "title": "Short catchy product name",
      "description_html": "<h2>…</h2><p class='p4'>…</p>",
      "tags": ["keyword1","keyword2","keyword3","keyword4","keyword5"]
    }}

    Context:
    - Title hint: {title_hint or 'none'}
    - Selected color names: {', '.join([c.get('title', '') for c in colors]) or 'none'}
    - Collection: {', '.join(body.get('collections') or []) or 'general'}
    """

    messages = [
        {"role": "system", "content": "You are an expert copywriter for Japanese travel-themed apparel."},
        {"role": "user", "content": [{"type": "text", "text": prompt_text}] + imgs},
    ]

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=messages,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        data["_debug_images_attached"] = len(imgs)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "_debug_images_attached": len(imgs)}), 400


@bp.post("/printify/products/<product_id>/save")
def api_printify_save(product_id):
    """
    Body (JSON):
    {
      "title": "New title",
      "description": "<h2>..</h2><p class='p4'>..</p>",
      "single_mode": true|false,
      "saved_light": [{"title":"Black","hex":"#000000"}, ...],
      "saved_dark":  [{"title":"White","hex":"#ffffff"}, ...]
    }
    Logic:
      - Reads local saved designs at data/designs/<product_id>/light.* and dark.* (if exist).
      - Uploads present files to Printify to get image_ids (skips DEFAULT_LOGO_ID).
      - Maps color titles -> variant_ids via product.options + variants.
      - Builds FRONT print_areas: one (single_mode) or two (split buckets).
      - PUTs {'title','description','print_areas'} to Printify.
      - Returns updated product JSON.
    """
    body = request.get_json(force=True) or {}
    title = body.get("title", "")
    description = body.get("description", "")
    single_mode = bool(body.get("single_mode", False))
    saved_default = body.get("saved_light") or []
    saved_other = body.get("saved_dark") or []

    raw_tags = body.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if isinstance(raw_tags, list):
        tags = []
        seen = set()
        for t in raw_tags:
            s = str(t).strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            tags.append(s)
        # (Optional) cap to a reasonable number if you like:
        tags = tags[:40]
    else:
        tags = []

    try:
        prod = printify.get_product(product_id)
    except Exception as e:
        return jsonify({"error": f"Failed to load product: {e}"}), 400

    # 1) Upload local design files if present (robust path & verbose logging)
    ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
    log = current_app.logger or logging.getLogger(__name__)

    def _candidate_design_dirs(pid: str) -> list[Path]:
        """
        Return a few candidate absolute directories where uploads may have been saved.
        Your /api/designs/upload route uses Path("data/designs") relative to the CWD,
        so we check that as well as paths relative to the Flask app root.
        """
        root = Path(current_app.root_path).resolve()
        # project root is usually one up from app/
        proj_root = root.parent
        return [
            proj_root / "data" / "designs" / pid,  # <project>/data/designs/<id>
            root / "data" / "designs" / pid,  # <project>/app/data/designs/<id> (fallback)
            Path("data/designs") / pid,  # CWD-relative fallback
        ]

    def _debug_list_dir(p: Path):
        try:
            if p.exists():
                items = [f.name for f in p.iterdir()]
                log.info("[SAVE] Dir exists %s: %r", str(p), items)
            else:
                log.info("[SAVE] Dir missing %s", str(p))
        except Exception as e:
            log.warning("[SAVE] Could not list %s: %s", str(p), e)

    def find_local(pid: str, which: str) -> tuple[Path, str] | None:

        """
        Search multiple locations for 'light.*' or 'dark.*'.
        Case-insensitive extension match; only allow image types.
        """
        hit = _resolve_from_manifest(pid, which)
        if hit:
            return hit
        return None

    def _design_root(pid: str) -> list[Path]:
        root = Path(current_app.root_path).resolve()
        proj_root = root.parent
        return [
            proj_root / "data" / "designs" / pid,  # <project>/data/designs/<pid>
            root / "data" / "designs" / pid,  # <project>/app/data/designs/<pid>
            Path("data/designs") / pid,  # CWD fallback
        ]

    def _load_manifest(base: Path) -> dict | None:
        mf = base / "manifest.json"
        if not mf.exists():
            return None
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            current_app.logger.warning("[SAVE] manifest.json unreadable at %s: %s", mf, e)
            return None

    def _resolve_from_manifest(pid: str, which: str) -> tuple[Path, str] | None:
        """
        Use manifest.json if present. Returns (absolute_path, original_file_name)
        """
        for base in _design_root(pid):
            mf = _load_manifest(base)
            if not mf:
                continue
            entry = (mf.get(which) or {})
            rel_path = entry.get("path")
            fname = entry.get("file")
            p = (base / fname).resolve()
            if p.exists() and p.suffix.lower() in ALLOWED_EXTS:
                return p, (fname or p.name)
        return None

    def ensure_upload_with_original_name(pid: str, which: str, path) -> str | None:
        if not path:
            return None

        up = printify.upload_image_file(file_path=str(path[0]), file_name=path[1])
        return up.get("id")

    def ensure_upload(path: Path | None) -> str | None:
        """
        Upload a local file to Printify and return its image id.
        Logs path, size, and response for debugging.
        """

        if not path:
            return None
        try:
            size = path.stat().st_size
        except Exception:
            size = -1
        log.info("[SAVE] Uploading %s (%s bytes)", str(path), size)
        try:
            up = printify.upload_image_file(file_path=str(path), file_name=path.name)
            iid = up.get("id")
            log.info("[SAVE] Uploaded %s => image_id=%s", path.name, iid)
            return iid
        except Exception as e:
            log.error("[SAVE] Upload failed for %s: %s", str(path), e)
            return None

    default_image_path = find_local(str(product_id), "light")
    dark_path = None if single_mode else find_local(str(product_id), "dark")

    default_img_id = ensure_upload_with_original_name(str(product_id), "default", default_image_path)
    dark_img_id = ensure_upload_with_original_name(str(product_id), "default", dark_path)

    # Fallbacks: if no uploaded images, try to reuse existing non-default FRONT images from product.
    DEFAULT_ID = getattr(Config, "DEFAULT_FRONT_IMAGE_ID", None)

    def _first_non_default_front_image(prod_json) -> str | None:
        for pa in (prod_json.get("print_areas") or []):
            for ph in (pa.get("placeholders") or []):
                if (ph.get("position") or "").strip().lower() != "front":
                    continue
                for img in (ph.get("images") or []):
                    iid = str(img.get("id") or "")
                    if not iid:
                        continue
                    if DEFAULT_ID and iid == str(DEFAULT_ID):
                        continue
                    return iid
        return None

    if not default_img_id:
        default_img_id = _first_non_default_front_image(prod)
        if default_img_id:
            log.info("[SAVE] Reusing existing FRONT image for light bucket: %s", default_img_id)
    if not single_mode and not dark_img_id:
        # For dual-mode, if we still have no dark image, reuse same as light (at least to satisfy validator)
        if default_img_id:
            dark_img_id = default_img_id
            log.info("[SAVE] Reusing light image id for dark bucket: %s", dark_img_id)

    # 2) Build color title -> variant_ids mapping
    # Get color titles from options[type='color']
    color_titles = set()
    for opt in (prod.get("options") or []):
        if (opt.get("type") == "color") or (str(opt.get("name", "")).lower() == "colors"):
            for v in (opt.get("values") or []):
                color_titles.add(str(v.get("title") or "").strip())

    def variant_color_title(var: dict) -> str | None:
        # variants often carry a 'title' like 'Black / M' OR options list/dict
        opts = var.get("options")
        if isinstance(opts, dict):
            cand = opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour")
            if cand: return str(cand).strip()
        elif isinstance(opts, list):
            for o in opts:
                try:
                    if (o.get("name") or "").strip().lower() in ("color", "colour"):
                        t = o.get("value") or o.get("title")
                        if t: return str(t).strip()
                except AttributeError:
                    pass
        t = var.get("title") or ""
        if " / " in t:
            return t.split(" / ")[0].strip()
        return None

    # gather variant ids by color title (enabled only)
    all_variants_by_color = {}
    for v in (prod.get("variants") or []):
        ct = variant_color_title(v)
        if not ct: continue
        all_variants_by_color.setdefault(ct, []).append(int(v["id"]))

    # Helpers to collect variant_ids from saved color pills
    def variant_ids_for(pills: list[dict]) -> list[int]:
        out = []
        for c in (pills or []):
            t = str(c.get("title") or "").strip()
            if not t: continue
            out.extend(all_variants_by_color.get(t, []))
        # de-dup preserve order
        seen = set()
        uniq = []
        for x in out:
            if x in seen: continue
            seen.add(x)
            uniq.append(x)
        return uniq

    enabled_default_vids = variant_ids_for(saved_default)

    # This will be the list of lists for all other colors as each needs its own placement
    enabled_other_vids = []
    if not single_mode:
        for c in saved_other:
            enabled_other_vids.append(variant_ids_for([c]))

    # Fallbacks if user hasn't picked any colors yet:
    if single_mode:
        if not enabled_default_vids:
            # use all enabled variants
            enabled_default_vids = [int(v["id"]) for v in (prod.get("variants") or []) if v.get("is_enabled", True)]
    else:
        # If one side empty, try infer from white vs others (optional simple fallback)
        if not enabled_default_vids and saved_default:
            enabled_default_vids = variant_ids_for(saved_default)
        if not enabled_other_vids and saved_other:
            enabled_other_vids = variant_ids_for(saved_other)

    # 3) Rebuild print_areas with variant-set buckets:
    #    - For each unique variant set, collect all placeholders (neck, sleeve, etc.)
    #      AND add our new front placeholders if applicable.

    def _slim_images(imgs: list[dict]) -> list[dict]:
        """Keep only real image assets (drop text layers)."""
        out = []
        for img in (imgs or []):
            iid = img.get("id")
            if not iid:
                continue
            t = (img.get("type") or "").lower()
            if t.startswith("text/") or ("input_text" in img):
                continue
            has_real_asset = bool(img.get("src") or img.get("url") or img.get("name"))
            if not has_real_asset:
                continue
            out.append({
                "id": iid,
                "x": float(img.get("x", 0.5)),
                "y": float(img.get("y", 0.5)),
                "scale": float(img.get("scale", 1.0)),
                "angle": int(float(img.get("angle", 0))),
            })
        return out

    all_selected_variant_ids = (enabled_default_vids +
                                [vid for color_variants in enabled_other_vids for vid in color_variants])

    all_variants_direct = [int(v["id"]) for v in (prod.get("variants") or [])]  # why do we need this again?
    all_variants_direct_sorted = sorted(all_variants_direct)

    # Build a map: tuple(sorted(variant_ids)) -> {"variant_ids":[...], "placeholders":[...]}
    areas_by_set: dict[tuple[int, ...], dict] = {}

    # 3a. Create buckets from color selections and add the front image to those buckets
    def _get_bucket(vids: list[int]) -> dict:
        key = tuple(sorted(int(x) for x in vids))
        if key not in areas_by_set:
            areas_by_set[key] = {"variant_ids": list(key), "placeholders": []}
        return areas_by_set[key]

    def _add_front_to_variants(vids: list[int], image_id: str | None):
        if not image_id or not vids:
            return
        bucket = _get_bucket(vids)
        bucket["placeholders"].append({
            "position": "front",
            "images": [{
                "id": image_id,
                "x": 0.5,
                "y": 0.5,
                "scale": 1.1375559820857382,
                "angle": 0
            }]
        })

    have_any_image = bool(default_img_id or dark_img_id)
    if have_any_image:
        if single_mode:
            _add_front_to_variants(all_variants_direct_sorted, (default_img_id or dark_img_id))
        else:
            assigned_vids = set(all_selected_variant_ids)
            unassigned = [vid for vid in all_variants_direct_sorted if
                          vid not in assigned_vids]

            assigned_default_vids = enabled_default_vids + unassigned
            bucket = _get_bucket(assigned_default_vids)
            _add_front_to_variants(assigned_default_vids, default_img_id)

            for one_color_vids in enabled_other_vids:
                bucket = _get_bucket(one_color_vids)
                _add_front_to_variants(one_color_vids, dark_img_id)

    # 3a. Preserve all non-front placeholders (neck, sleeve, etc.)
    for area in (prod.get("print_areas") or []):
        for bucket in areas_by_set.values():
            bucket = _get_bucket(bucket["variant_ids"])
            for ph in (area.get("placeholders") or []):
                pos = (ph.get("position") or "").strip().lower()
                if pos == "front":
                    continue
                slim = _slim_images(ph.get("images") or [])
                if not slim:
                    continue
                slim_ph = {"position": ph.get("position"), "images": slim}
                if ph.get("decoration_method"):
                    slim_ph["decoration_method"] = ph["decoration_method"]
                bucket["placeholders"].append(slim_ph)

    # 3c. Build final merged list — one element per unique variant set
    merged_areas = [v for v in areas_by_set.values() if v.get("placeholders")]

    # Ensure coverage: union of variant_ids across ALL areas should cover all enabled variants.
    def _covered(areas):
        s = set()
        for a in (areas or []):
            for vid in (a.get("variant_ids") or []):
                try:
                    s.add(int(vid))
                except Exception:
                    pass
        return s

    include_print_areas = False
    if merged_areas:
        covered = _covered(merged_areas)
        missing = [vid for vid in unassigned if vid not in covered]
        if missing and (default_img_id or dark_img_id):
            # add a final FRONT-only area for missing variants (use whatever image we have)
            merged_areas.append({
                "variant_ids": sorted(missing),
                "placeholders": [{
                    "position": "front",
                    "images": [{
                        "id": (default_img_id or dark_img_id),
                        "x": 0.5, "y": 0.5, "scale": 1.1375559820857382, "angle": 0
                    }]
                }]
            })
            include_print_areas = True
        else:
            include_print_areas = True

    # Collect every variant id referenced by the print_areas we’re about to send
    def _variant_ids_from_print_areas(areas: list[dict]) -> set[int]:
        out = set()
        for a in (areas or []):
            for vid in (a.get("variant_ids") or []):
                try:
                    out.add(int(vid))
                except:
                    pass
        return out

    must_enable = _variant_ids_from_print_areas(merged_areas)

    def _coerce_int(x, default=0):
        try:
            return int(str(x))
        except Exception:
            return default

    # We'll now update the variant list with what's actually enabled.
    # Keep a generous set of keys that the API tolerates.
    # (Printify ignores unknown fields, but these are commonly present/accepted.)
    _allowed_variant_keys = {
        "id", "price", "is_enabled", "sku", "options", "is_default", "title", "grams"
    }

    must_enable = all_selected_variant_ids
    variants_patch = []
    for v in (prod.get("variants") or []):
        vv = dict(v)  # shallow copy of original
        vv["id"] = _coerce_int(v.get("id"))
        # price must be an int (cents). Keep original; DO NOT invent prices.
        if "price" in v:
            vv["price"] = _coerce_int(v["price"])
        # In case some providers don’t return price (rare), fall back to 0 to satisfy schema.
        # (Ideally you never hit this path; better to read price from the template product.)
        else:
            vv["price"] = 0

        # enable if referenced by any print_areas; otherwise keep as-is
        vv["is_enabled"] = True if vv["id"] in must_enable else False

        # trim to allowed keys to avoid noisy payloads
        vv = {k: vv[k] for k in _allowed_variant_keys if k in vv}
        variants_patch.append(vv)

    # include in the outgoing patch
    patch = {"title": title, "description": description}
    if tags:  # only include if provided to avoid overwriting unintentionally
        patch["tags"] = tags
    if include_print_areas:
        patch["print_areas"] = merged_areas
    patch["variants"] = variants_patch

    # Debug: log what we’re about to send
    try:
        current_app.logger.info(
            "SAVE debug: include_print_areas=%s areas_sent=%s have_any_image=%s light_img_id=%s dark_img_id=%s",
            include_print_areas, len(patch.get("print_areas", [])),
            bool(default_img_id or dark_img_id), bool(default_img_id), bool(dark_img_id)
        )
    except Exception:
        pass

    # If absolutely nothing to update, bail clearly (prevents returning None)
    if not include_print_areas and (title.strip() == "" and description.strip() == ""):
        return jsonify({"error": "Nothing to save: no title/description changes and no print_areas to update."}), 400

    # Perform the update and always return a response
    try:
        updated_meta = printify.update_product(product_id, patch)
    except Exception as e:
        return jsonify({"error": str(e), "payload": patch}), 400

    # Try to fetch the refreshed product; fall back to the update response if fetch fails
    try:
        refreshed = printify.get_product(product_id)
    except Exception:
        refreshed = updated_meta

    return jsonify({
        "ok": True,
        "product": refreshed,
        "sent": {
            "title": patch.get("title"),
            "description": bool(patch.get("description")),
            "print_areas_count": len(patch.get("print_areas", []))
        }
    })

@bp.post("/printify/products/<product_id>/refresh")
def api_printify_refresh(product_id):
    import traceback, re

    def _json_error(msg, *, status=400, detail=None):
        payload = {"error": msg}
        if detail:
            payload["detail"] = detail
        return jsonify(payload), status

    try:
        prod = printify.get_product(product_id)
    except Exception as e:
        return _json_error("Failed to load product from Printify",
                           detail={"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()})

    try:
        # Reuse the same normalizer we use for the bulk cache
        normalized = _normalize_printify_for_cache(prod)

        pid = normalized["id"]

        # Safer cache write: normalize whatever store.list() returns into a dict
        try:
            existing_any = store.list(PRINTIFY_PRODUCTS_COLLECTION)
        except Exception:
            existing_any = None

        cache_map = {}
        if isinstance(existing_any, dict):
            cache_map = {str(k): v for k, v in existing_any.items()}
        elif isinstance(existing_any, list):
            # convert list of items into {id: item}
            for it in existing_any:
                if not isinstance(it, dict):
                    continue
                key = str(it.get("id") or it.get("_id") or "").strip()
                if key:
                    cache_map[key] = it
        else:
            # None or unexpected → start fresh
            cache_map = {}

        cache_map[str(pid)] = normalized

        # Write back atomically
        store.replace_collection(PRINTIFY_PRODUCTS_COLLECTION, cache_map)

        return jsonify({"ok": True, "product": prod, "normalized": normalized})
    except Exception as e:
        # Return rich diagnostics so the frontend alert has something useful
        return _json_error("Failed to normalize or cache product",
                           detail={"type": type(e).__name__, "msg": str(e), "trace": traceback.format_exc()})
