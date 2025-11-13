from flask import Blueprint, request, jsonify, current_app

from ..extensions import store, shopify_client as shopify
from .. import Config
from ..extensions import printify_client as printify
from pathlib import Path
import httpx

from ..utils.mockups import generate_mockups_for_design

bp = Blueprint("shopify_api", __name__)

SHOPIFY_PRODUCTS_COLLECTION = "shopify_products"


def _normalize_product_tags(product: dict) -> dict:
    """Normalize tags from comma-separated string to array format for database storage."""
    if product and "tags" in product:
        raw_tags = product["tags"]
        if isinstance(raw_tags, str):
            product["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif not isinstance(raw_tags, list):
            product["tags"] = []
    return product


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@bp.post("/shopify/products/<product_id>/images")
def shopify_upload_images(product_id):
    payload = request.json or {}
    image_paths = payload.get("image_paths", [])
    uploaded = shopify.upload_product_images(product_id, image_paths)
    return jsonify({"uploaded": uploaded})


@bp.post("/shopify/products/<product_id>/save")
def api_shopify_save(product_id):
    """
    Update a Shopify product (title, description, tags, status).
    Always returns JSON.
    """
    try:
        body = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"Bad JSON in request: {e}"}), 400

    title = (body.get("title") or "").strip()
    desc = (body.get("description") or "").strip()
    tags = body.get("tags") or []
    status = body.get("status") or "active"

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    payload = {}
    if title:
        payload["title"] = title
    payload["body_html"] = desc
    payload["tags"] = ", ".join(tags)
    if status in ("active", "draft"):
        payload["status"] = status

    try:
        # Get existing product from cache to preserve fields we're not updating
        existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
        
        # Update product on Shopify
        updated = shopify.update_product(product_id, payload)
        
        # Normalize tags from comma-separated string to array for database storage
        updated = _normalize_product_tags(updated)
        
        # Merge only the fields we updated into the existing cached product
        # This preserves images, variants, options, and other complex structures
        merged = existing.copy()
        if updated:
            # Update only the fields we explicitly changed
            if "title" in updated:
                merged["title"] = updated["title"]
            if "body_html" in updated:
                merged["body_html"] = updated["body_html"]
            if "tags" in updated:
                merged["tags"] = updated["tags"]
            if "status" in updated:
                merged["status"] = updated["status"]
            # Update timestamp if provided
            if "updated_at" in updated:
                merged["updated_at"] = updated["updated_at"]
            # Update simple fields if missing, but don't overwrite existing ones
            # (to avoid losing data if Shopify response is incomplete)
            for key in ["handle", "vendor", "product_type"]:
                if key in updated:
                    # Only update if we don't have it, or if it's a simple string update
                    if key not in merged or not merged.get(key):
                        merged[key] = updated[key]
            # created_at should never change, only set if missing
            if "created_at" in updated and "created_at" not in merged:
                merged["created_at"] = updated["created_at"]
            
            # Explicitly preserve complex nested structures from cache
            # These should NOT be overwritten by the update response
            # as they may be incomplete or missing in the update response
            preserve_fields = ["images", "image", "variants", "options", "primary_image"]
            for field in preserve_fields:
                if field in existing:
                    merged[field] = existing[field]
        
        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), merged)
        return jsonify({"ok": True, "updated": merged})
    except Exception as e:
        current_app.logger.exception("Shopify update failed")
        # Return a real JSON error so frontend `.json()` won't choke
        return jsonify({"error": str(e)}), 500


@bp.post("/shopify/products/<product_id>/refresh")
def api_shopify_refresh(product_id):
    """Fetch latest data from Shopify and refresh cache"""
    try:
        product = shopify.get_product(product_id)
        if product:
            # Normalize tags from comma-separated string to array for database storage
            product = _normalize_product_tags(product)
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), product)
            return jsonify({"ok": True, "product": product})
        return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        current_app.logger.exception("Shopify refresh failed")
        return jsonify({"error": str(e)}), 400


@bp.post("/shopify/products/<product_id>/generate_mockups")
def api_shopify_generate_mockups(product_id):
    """Generate flat-lay mockups for a Shopify product.

    Flow:
      - Find associated Printify product in cached printify_products (shopify_product_id)
      - Load Printify product JSON and find a FRONT image src for the design
      - Download the image (or use local /designs/... path) and composite it onto
        templates in assets/mockups/g64k using generate_mockups_for_design
      - Save outputs to assets/product_mockups/<product_id> and return their paths
    """
    # 1) Find printify product that links to this shopify product
    pf = None
    for item in store.list("printify_products"):
        if str(item.get("shopify_product_id") or "") == str(product_id):
            pf = item
            break
    if not pf:
        return jsonify({"error": "No associated Printify product found in cache"}), 404

    printify_id = str(pf.get("id") or pf.get("_id") or pf.get("id"))
    try:
        prod = printify.get_product(printify_id)
    except Exception as e:
        current_app.logger.exception("Failed to fetch Printify product %s", printify_id)
        return jsonify({"error": f"Failed to fetch Printify product: {e}"}), 400

    # 2) Find a front placeholder image src
    src = None
    for pa in (prod.get("print_areas") or []):
        for ph in (pa.get("placeholders") or []):
            if (ph.get("position") or "").lower() != "front":
                continue
            for img in (ph.get("images") or []):
                candidate = img.get("src") or img.get("url")
                if candidate:
                    src = candidate
                    break
            if src:
                break
        if src:
            break

    # fallback to preview or images
    if not src:
        src = prod.get("preview") or (prod.get("images") or [None])[0]

    if not src:
        return jsonify({"error": "Could not find a front design image on Printify product"}), 404

    # 3) Resolve local design path (if /designs/) or download remote URL to tmp
    design_local_path = None
    try:
        if str(src).startswith("/designs/"):
            # local saved route -> map to filesystem like other routes in the app
            p = Path("." + str(src))
            if not p.exists():
                return jsonify({"error": f"Local design path not found: {p}"}), 404
            design_local_path = str(p)
        else:
            # download
            tmpdir = Path("data/tmp")
            tmpdir.mkdir(parents=True, exist_ok=True)
            suffix = Path(src).suffix or ".png"
            outtmp = tmpdir / f"shopify_{product_id}_design{suffix}"
            with httpx.Client(timeout=30) as client:
                r = client.get(src)
                r.raise_for_status()
                outtmp.write_bytes(r.content)
            design_local_path = str(outtmp)
    except Exception as e:
        current_app.logger.exception("Failed to obtain design image")
        return jsonify({"error": f"Failed to obtain design image: {e}"}), 400

    # 4) Collect template files from assets/mockups/g64k
    templates_dir = Config.ASSETS_DIR / "mockups" / "g64k"
    if not templates_dir.exists():
        return jsonify({"error": f"Templates folder missing: {templates_dir}"}), 500
    templates = [str(p) for p in sorted(templates_dir.iterdir()) if p.suffix.lower() in Config.ALLOWED_EXTS]
    if not templates:
        return jsonify({"error": "No template images found in assets/mockups/g64k"}), 500

    # --- Build variant -> front image src mapping from Printify print_areas ---
    def _front_src_from_pa(pa: dict) -> str | None:
        # Prefer explicit "front" placeholder
        for ph in (pa.get("placeholders") or []):
            if str(ph.get("position", "")).lower() == "front":
                for img in (ph.get("images") or []):
                    if isinstance(img, dict) and (img.get("src") or img.get("url")):
                        return img.get("src") or img.get("url")
        # Fallback: any placeholder with an image
        for ph in (pa.get("placeholders") or []):
            for img in (ph.get("images") or []):
                if isinstance(img, dict) and (img.get("src") or img.get("url")):
                    return img.get("src") or img.get("url")
        return None

    # Helper to normalize color/title strings for matching (defined early so other maps can use it)
    def _norm(s: str) -> str:
        return (s or "").strip().lower()

    # Build mapping from Printify variant ID -> color title (from Printify product variants)
    printify_variant_id_to_title: dict[int, str] = {}
    for pv in (prod.get("variants") or []):
        try:
            pvid = int(pv.get("id"))
        except Exception:
            continue
        # Try option1/option2, options list, then title fallback
        p_ctitle = None
        if pv.get("option1"):
            p_ctitle = pv.get("option1")
        elif pv.get("option2"):
            p_ctitle = pv.get("option2")
        else:
            opts = pv.get("options")
            if isinstance(opts, list):
                for o in opts:
                    try:
                        name = (o.get("name") or "").strip().lower()
                        if name in ("color", "colour"):
                            p_ctitle = o.get("value") or o.get("title")
                            break
                    except Exception:
                        continue
            if not p_ctitle and pv.get("title"):
                t = pv.get("title")
                p_ctitle = t.split(" / ")[0] if " / " in t else t
        if p_ctitle:
            printify_variant_id_to_title[pvid] = str(p_ctitle).strip()

    # Build color_title (normalized) -> print_area front-src mapping using Printify print_areas
    color_title_to_pa_src: dict[str, str] = {}
    for pa in (prod.get("print_areas") or []):
        pa_src = _front_src_from_pa(pa)
        if not pa_src:
            continue
        for v in (pa.get("variant_ids") or []):
            try:
                v_int = int(v)
            except Exception:
                continue
            ptitle = printify_variant_id_to_title.get(v_int)
            if ptitle:
                color_title_to_pa_src[_norm(ptitle)] = pa_src

    # map template stem normalized -> template full path
    template_map: dict[str, str] = {}
    for t in templates:
        stem = Path(t).stem
        template_map[_norm(stem)] = t

    # Load color catalog (if present) to map template names -> hex
    colors_file = templates_dir / "colors.json"
    template_hex_map: dict[str, str] = {}
    hex_to_template_names: dict[str, list[str]] = {}
    try:
        import json as _json
        if colors_file.exists():
            colors_data = _json.loads(colors_file.read_text(encoding="utf-8"))
            for entry in (colors_data or []):
                title = (entry.get("Color") or entry.get("Color") or entry.get("color") or entry.get("Color"))
                hexv = entry.get("Hex") or entry.get("Hex") or entry.get("hex") or entry.get("Hex")
                if not title or not hexv:
                    continue
                norm_title = _norm(str(title))
                norm_hex = str(hexv).lstrip("#").upper()
                template_hex_map[norm_title] = norm_hex
                hex_to_template_names.setdefault(norm_hex, []).append(norm_title)
    except Exception:
        # Non-fatal; if parsing fails we'll rely on variant->title mapping
        template_hex_map = {}
        hex_to_template_names = {}
    # Keep a list of printify-normalized color titles to aid fuzzy matching
    try:
        import difflib as _difflib
    except Exception:
        _difflib = None

    # Build a map of print_area background hex -> src (if given)
    pa_bg_map: dict[str, str] = {}
    for pa in (prod.get("print_areas") or []):
        bg = pa.get("background")
        pa_src = _front_src_from_pa(pa)
        if not pa_src:
            continue
        if isinstance(bg, str) and bg:
            norm_bg = str(bg).lstrip("#").upper()
            pa_bg_map.setdefault(norm_bg, pa_src)

    # Determine colors actually in use (enabled variants only) and map variant -> color title
    # Try to load Shopify product from cache, fall back to live fetch
    try:
        shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id)
        if not shop_product:
            shop_product = shopify.get_product(product_id)
    except Exception:
        current_app.logger.exception("Failed to load Shopify product for variant/color mapping; will use cache or empty list")
        shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or {}

    variants = (shop_product.get("variants") or [])

    variant_to_title: dict[int, str] = {}
    used_titles: set[str] = set()
    for var in variants:
        try:
            if var.get("is_enabled", True) is False:
                continue
            vid = int(var.get("id") or 0)
            # Shopify variant color may be in option1/option2, in options list, or embedded in title like "Black / S"
            ctitle = None
            if var.get("option1"):
                ctitle = var.get("option1")
            elif var.get("option2"):
                ctitle = var.get("option2")
            else:
                opts = var.get("options")
                if isinstance(opts, list):
                    for o in opts:
                        try:
                            name = (o.get("name") or "").strip().lower()
                            if name in ("color", "colour"):
                                ctitle = o.get("value") or o.get("title")
                                break
                        except Exception:
                            continue
                if not ctitle and var.get("title"):
                    t = var.get("title")
                    ctitle = t.split(" / ")[0] if " / " in t else t
            if ctitle:
                ctitle = str(ctitle).strip()
                variant_to_title[vid] = ctitle
                used_titles.add(_norm(ctitle))
        except Exception:
            continue

    # Pick templates whose normalized stem matches a used title
    templates_to_generate = []
    for norm_title in used_titles:
        if norm_title in template_map:
            templates_to_generate.append(template_map[norm_title])

    # If we couldn't match any templates, fallback to generating for all templates (preserve previous behavior)
    if not templates_to_generate:
        current_app.logger.info("No template names matched Shopify variant colors; generating all templates")
        templates_to_generate = templates.copy()

    # 5) For each template, determine the correct design image (per-color if available) and generate mockup
    out_dir = Config.ASSETS_DIR / "product_mockups" / str(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _download_to_tmp(src: str, stem: str) -> str:
        """Download src to data/tmp and return local path (empty string on failure)."""
        try:
            if str(src).startswith("/designs/"):
                p = Path("." + str(src))
                if p.exists():
                    return str(p)
                return ""
            tmpdir = Path("data/tmp")
            tmpdir.mkdir(parents=True, exist_ok=True)
            suffix = Path(src).suffix or ".png"
            outtmp = tmpdir / f"shopify_{product_id}_{stem}_design{suffix}"
            with httpx.Client(timeout=30) as client:
                r = client.get(src)
                r.raise_for_status()
                outtmp.write_bytes(r.content)
            return str(outtmp)
        except Exception:
            return ""

    out_files = []
    for t in templates_to_generate:
        stem = Path(t).stem
        norm_stem = _norm(stem)

        # Prefer a per-color Printify print_area src (mapped by normalized color title)
        design_src_for_template = color_title_to_pa_src.get(norm_stem)

        # Next prefer print_area background color match using colors.json
        if not design_src_for_template:
            tmpl_hex = template_hex_map.get(_norm(stem))
            if tmpl_hex and tmpl_hex in pa_bg_map:
                design_src_for_template = pa_bg_map.get(tmpl_hex)

        # Try fuzzy match between template name and Printify color titles
        if not design_src_for_template and _difflib:
            candidates = list(color_title_to_pa_src.keys())
            if candidates:
                matches = _difflib.get_close_matches(norm_stem, candidates, n=1, cutoff=0.7)
                if matches:
                    design_src_for_template = color_title_to_pa_src.get(matches[0])

        # Cross-match via colors.json: if a Printify color maps to the same hex as the template, use its pa_src
        if not design_src_for_template and template_hex_map:
            tmpl_hex = template_hex_map.get(_norm(stem))
            if tmpl_hex:
                for ptitle_norm, pa_src in color_title_to_pa_src.items():
                    p_hex = template_hex_map.get(ptitle_norm)
                    if p_hex and p_hex == tmpl_hex:
                        design_src_for_template = pa_src
                        break

        # Fallback to the generic product src we found earlier
        if not design_src_for_template:
            # previous single source resolution (from earlier in this function)
            for pa in (prod.get("print_areas") or []):
                # reuse same helper to try to find any front src
                s = _front_src_from_pa(pa)
                if s:
                    design_src_for_template = s
                    break

        # Download/resolve local design image path for this template
        template_design_local = None
        if design_src_for_template:
            template_design_local = _download_to_tmp(design_src_for_template, stem)

        # Final fallback to global design_local_path (single preview we downloaded earlier)
        if not template_design_local:
            template_design_local = design_local_path

        # If still no design available, skip this template
        if not template_design_local:
            current_app.logger.warning("No design image available for template %s; skipping", stem)
            continue

        # Generate mockup for this single template
        try:
            generated = generate_mockups_for_design(
                design_png_path=template_design_local,
                templates=[t],
                placements={},
                out_dir=out_dir,
                scale=1.0,
            )
        except Exception as e:
            current_app.logger.exception("Mockup generation failed for template %s", stem)
            continue

        # Rename output mockup_{stem}.png -> {stem}.png and collect
        gen_path = out_dir / f"mockup_{stem}.png"
        final_name = out_dir / f"{stem}.png"
        try:
            if gen_path.exists():
                if final_name.exists():
                    final_name.unlink(missing_ok=True)
                gen_path.replace(final_name)
                out_files.append(final_name)
        except Exception:
            if final_name.exists():
                out_files.append(final_name)

    # 7) Produce relative paths for client preview (relative to project base)
    rel_out = [str(p.relative_to(Config.BASE_DIR)) for p in sorted(out_files)]

    # 8) Build mapping of variant IDs -> mockup paths for variants whose color matched one of the generated templates
    variants_to_update: dict[int, str] = {}
    # Create reverse map: normalized template stem -> relative output path
    stem_to_relpath: dict[str, str] = {}
    for p in out_files:
        stem = _norm(Path(p).stem)
        try:
            stem_to_relpath[stem] = str(p.relative_to(Config.BASE_DIR))
        except Exception:
            stem_to_relpath[stem] = str(p)

    for vid, title in variant_to_title.items():
        n = _norm(title)
        if n in stem_to_relpath:
            variants_to_update[vid] = stem_to_relpath[n]

    return jsonify({"mockups": rel_out, "variants_to_update": variants_to_update})


@bp.post("/shopify/products/<product_id>/apply_mockups")
def api_shopify_apply_mockups(product_id: str):
    """Upload generated mockups and attach them to Shopify variants.

    Request JSON shape:
      {
        "variants_to_update": {"<variant_id>": "path/to/mockup.png", ...},
        "default_variant_id": "<variant_id>"  // optional - sets the product default image
      }

    The endpoint will:
      - Upload each provided mockup file to Shopify for the product
      - Call Shopify product update to attach image ids to the given variant_ids
      - Optionally set product.image to the chosen default image id
      - Refresh the cached product in our store and return the updated product JSON
    """
    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        return jsonify({"error": f"Bad JSON in request: {e}"}), 400

    variants_map = body.get("variants_to_update") or {}
    if not isinstance(variants_map, dict) or not variants_map:
        return jsonify({"error": "Missing or invalid 'variants_to_update' map"}), 400

    default_variant_id = body.get("default_variant_id")

    # Resolve paths and verify files exist
    files_to_upload: list[tuple[str, int]] = []  # (path, variant_id)
    for vid_str, rel_path in variants_map.items():
        try:
            vid = int(vid_str)
        except Exception:
            return jsonify({"error": f"Invalid variant id: {vid_str}"}), 400
        # If path is already absolute or relative to BASE_DIR
        p = Path(rel_path)
        if not p.is_absolute():
            p = Config.BASE_DIR / rel_path
        if not p.exists():
            return jsonify({"error": f"Mockup file not found: {p}"}), 404
        files_to_upload.append((str(p), vid))

    uploaded_images: list[dict] = []
    image_map_by_variant: dict[int, dict] = {}
    errors = []

    # Upload files one-by-one so we can capture returned image ids
    for file_path, vid in files_to_upload:
        try:
            # Reuse shopify.upload_product_images which accepts a list
            res = shopify.upload_product_images(product_id, [file_path])
            if not res or not isinstance(res, list):
                raise RuntimeError(f"Unexpected upload response: {res}")
            info = res[0]
            # Response shape may be {"image": {...}} or {...}
            img_obj = info.get("image") if isinstance(info, dict) and info.get("image") else info
            image_id = None
            if isinstance(img_obj, dict):
                image_id = img_obj.get("id") or img_obj.get("id")
            if not image_id:
                raise RuntimeError(f"Could not determine image id for uploaded file {file_path}: {info}")
            uploaded_images.append({"file": file_path, "image_id": int(image_id)})
            image_map_by_variant[vid] = {"image_id": int(image_id), "file": file_path}
        except Exception as e:
            current_app.logger.exception("Failed to upload mockup %s", file_path)
            errors.append({"file": file_path, "error": str(e)})

    if not uploaded_images:
        return jsonify({"error": "No images were uploaded", "details": errors}), 500

    # Build images payload for Shopify update: include image ids + variant associations
    images_payload = []
    for vid, info in image_map_by_variant.items():
        images_payload.append({"id": info["image_id"], "variant_ids": [int(vid)]})

    # Determine default image id to set: prefer provided default_variant_id else first uploaded
    default_image_id = None
    if default_variant_id:
        try:
            dvid = int(default_variant_id)
            default_image_id = image_map_by_variant.get(dvid, {}).get("image_id")
        except Exception:
            default_image_id = None
    if not default_image_id:
        # pick first uploaded as default
        default_image_id = uploaded_images[0]["image_id"]

    # Prepare update payload: images list will attach variant_ids; set product.image to default
    update_payload = {"images": images_payload}
    if default_image_id:
        update_payload["image"] = {"id": int(default_image_id)}

    try:
        updated = shopify.update_product(product_id, update_payload)
    except Exception as e:
        current_app.logger.exception("Failed to update Shopify product images for %s", product_id)
        return jsonify({"error": f"Failed to update Shopify product: {e}", "uploads": uploaded_images, "errors": errors}), 500

    # Refresh cached product in our store
    refreshed = None
    try:
        refreshed = shopify.get_product(product_id)
        if refreshed:
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
    except Exception:
        current_app.logger.exception("Failed to refresh Shopify product after image update")

    resp = {
        "ok": True,
        "uploaded": uploaded_images,
        "errors": errors,
        "updated_product": (refreshed if refreshed is not None else updated)
    }
    return jsonify(resp)


@bp.post("/shopify/products/<product_id>/apply_generated_mockups")
def api_shopify_apply_generated_mockups(product_id: str):
    """Automatically find generated mockups for product_id, map them to Shopify variants by color,
    upload the images to Shopify, attach them to the matching variant_ids and set the default image.

    Optional JSON body:
      { "default_variant_id": "<variant_id>" }

    Returns upload results and the refreshed product.
    """
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    default_variant_id = body.get("default_variant_id")

    # 1) Locate generated mockups folder
    folder = Config.ASSETS_DIR / "product_mockups" / str(product_id)
    if not folder.exists():
        return jsonify({"error": f"No generated mockups folder found for product {product_id}"}), 404

    files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in Config.ALLOWED_EXTS]
    if not files:
        return jsonify({"error": "No generated mockup image files found"}), 404

    # Build template stem -> path map
    def _norm_local(s: str) -> str:
        return (s or "").strip().lower()

    stem_to_path: dict[str, Path] = {}
    for p in files:
        stem_to_path[_norm_local(p.stem)] = p

    # 2) Load Shopify product (cache then live)
    try:
        shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id)
        if not shop_product:
            shop_product = shopify.get_product(product_id)
    except Exception:
        current_app.logger.exception("Failed to load Shopify product for apply_generated_mockups")
        shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or {}

    if not shop_product:
        return jsonify({"error": "Shopify product not found (cache or API)"}), 404

    # 3) Build Shopify variant id -> normalized color title map
    variant_to_title_local: dict[int, str] = {}
    for var in (shop_product.get("variants") or []):
        try:
            if var.get("is_enabled", True) is False:
                continue
            vid = int(var.get("id") or 0)
            ctitle = None
            if var.get("option1"):
                ctitle = var.get("option1")
            elif var.get("option2"):
                ctitle = var.get("option2")
            else:
                opts = var.get("options")
                if isinstance(opts, list):
                    for o in opts:
                        try:
                            name = (o.get("name") or "").strip().lower()
                            if name in ("color", "colour"):
                                ctitle = o.get("value") or o.get("title")
                                break
                        except Exception:
                            continue
                if not ctitle and var.get("title"):
                    t = var.get("title")
                    ctitle = t.split(" / ")[0] if " / " in t else t
            if ctitle:
                variant_to_title_local[vid] = str(ctitle).strip()
        except Exception:
            continue

    if not variant_to_title_local:
        return jsonify({"error": "No Shopify variants found or could not resolve variant colors"}), 400

    # 4) Map variants to mockup files by normalized title -> stem path
    variants_to_file: dict[int, str] = {}
    unmatched_variants: list[int] = []
    for vid, title in variant_to_title_local.items():
        n = _norm_local(title)
        if n in stem_to_path:
            variants_to_file[vid] = str(stem_to_path[n].relative_to(Config.BASE_DIR))
        else:
            unmatched_variants.append(vid)

    # Try a fuzzy match for unmatched variants
    if unmatched_variants:
        try:
            import difflib as _difflib
            candidates = list(stem_to_path.keys())
            for vid in list(unmatched_variants):
                n = _norm_local(variant_to_title_local.get(vid, ""))
                matches = _difflib.get_close_matches(n, candidates, n=1, cutoff=0.65)
                if matches:
                    variants_to_file[vid] = str(stem_to_path[matches[0]].relative_to(Config.BASE_DIR))
                    unmatched_variants.remove(vid)
        except Exception:
            pass

    if not variants_to_file:
        return jsonify({"error": "Could not match any Shopify variant colors to generated mockups"}), 400

    # 5) Upload matched mockups to Shopify and attach to variants
    files_to_upload = {}
    for vid, rel in variants_to_file.items():
        # resolve absolute path
        p = Path(rel)
        if not p.is_absolute():
            p = Config.BASE_DIR / rel
        files_to_upload.setdefault(str(p), []).append(int(vid))

    uploaded_images = []
    path_to_image_id: dict[str, int] = {}
    errors = []

    for path_str, vids in files_to_upload.items():
        try:
            res = shopify.upload_product_images(product_id, [path_str])
            if not res or not isinstance(res, list):
                raise RuntimeError(f"Unexpected upload response: {res}")
            info = res[0]
            img_obj = info.get("image") if isinstance(info, dict) and info.get("image") else info
            image_id = None
            if isinstance(img_obj, dict):
                image_id = img_obj.get("id") or img_obj.get("id")
            if not image_id:
                raise RuntimeError(f"Could not determine image id for uploaded file {path_str}: {info}")
            path_to_image_id[path_str] = int(image_id)
            uploaded_images.append({"file": path_str, "image_id": int(image_id), "variant_ids": vids})
        except Exception as e:
            current_app.logger.exception("Failed to upload mockup %s", path_str)
            errors.append({"file": path_str, "error": str(e)})

    if not uploaded_images:
        return jsonify({"error": "No images were uploaded", "details": errors}), 500

    # 6) Build images payload for Shopify update
    images_payload = []
    for path_str, vids in files_to_upload.items():
        img_id = path_to_image_id.get(path_str)
        if img_id:
            images_payload.append({"id": img_id, "variant_ids": vids})

    # 7) Determine default image id to set
    default_image_id = None
    if default_variant_id:
        try:
            dvid = int(default_variant_id)
            # find corresponding path
            dpath = variants_to_file.get(dvid)
            if dpath:
                p_abs = Config.BASE_DIR / dpath if not Path(dpath).is_absolute() else Path(dpath)
                default_image_id = path_to_image_id.get(str(p_abs))
        except Exception:
            default_image_id = None

    # If still none, try to pick a variant flagged as default in Shopify
    if not default_image_id:
        for v in (shop_product.get("variants") or []):
            try:
                if v.get("is_default") and int(v.get("id")) in variants_to_file:
                    p_rel = variants_to_file[int(v.get("id"))]
                    p_abs = Config.BASE_DIR / p_rel if not Path(p_rel).is_absolute() else Path(p_rel)
                    default_image_id = path_to_image_id.get(str(p_abs))
                    break
            except Exception:
                continue

    # Fallback: first uploaded image
    if not default_image_id:
        default_image_id = uploaded_images[0].get("image_id")

    update_payload = {"images": images_payload}
    if default_image_id:
        update_payload["image"] = {"id": int(default_image_id)}

    try:
        updated = shopify.update_product(product_id, update_payload)
    except Exception as e:
        current_app.logger.exception("Failed to update Shopify product images for %s", product_id)
        return jsonify({"error": f"Failed to update Shopify product: {e}", "uploads": uploaded_images, "errors": errors}), 500

    # Refresh cached product
    try:
        refreshed = shopify.get_product(product_id)
        if refreshed:
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
    except Exception:
        current_app.logger.exception("Failed to refresh Shopify product after image update")

    resp = {
        "ok": True,
        "uploaded": uploaded_images,
        "errors": errors,
        "updated_product": (refreshed if refreshed is not None else updated)
    }
    return jsonify(resp)
