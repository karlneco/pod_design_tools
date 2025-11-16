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


# ========================================
# Helper functions for generate_mockups
# ========================================

def _normalize_str(s: str) -> str:
    """Normalize string for color/title matching."""
    return (s or "").strip().lower()


def _find_printify_product_by_shopify_id(product_id: str) -> dict | None:
    """Find Printify product associated with Shopify product ID."""
    for item in store.list("printify_products"):
        if str(item.get("shopify_product_id") or "") == str(product_id):
            return item
    return None


def _extract_front_design_src(prod: dict) -> str | None:
    """Extract front design image source from Printify product."""
    # Try print areas first
    for pa in (prod.get("print_areas") or []):
        for ph in (pa.get("placeholders") or []):
            if (ph.get("position") or "").lower() != "front":
                continue
            for img in (ph.get("images") or []):
                candidate = img.get("src") or img.get("url")
                if candidate:
                    return candidate
    
    # Fallback to preview or images
    src = prod.get("preview")
    if src:
        return src
    
    images = prod.get("images") or []
    return images[0] if images else None


def _resolve_design_path(src: str, product_id: str) -> str:
    """Resolve design image to local path (download if remote URL)."""
    if str(src).startswith("/designs/"):
        # Local saved route
        p = Path("." + str(src))
        if not p.exists():
            raise FileNotFoundError(f"Local design path not found: {p}")
        return str(p)
    
    # Download remote URL
    tmpdir = Path("data/tmp")
    tmpdir.mkdir(parents=True, exist_ok=True)
    suffix = Path(src).suffix or ".png"
    outtmp = tmpdir / f"shopify_{product_id}_design{suffix}"
    
    with httpx.Client(timeout=30) as client:
        r = client.get(src)
        r.raise_for_status()
        outtmp.write_bytes(r.content)
    
    return str(outtmp)


def _get_front_src_from_print_area(pa: dict) -> str | None:
    """Extract front image source from a print area."""
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


def _extract_color_from_variant(variant: dict) -> str | None:
    """Extract color title from variant (option1, option2, options list, or title)."""
    # Try option1/option2 first
    if variant.get("option1"):
        return variant.get("option1")
    if variant.get("option2"):
        return variant.get("option2")
    
    # Try options list
    opts = variant.get("options")
    if isinstance(opts, list):
        for o in opts:
            try:
                name = (o.get("name") or "").strip().lower()
                if name in ("color", "colour"):
                    return o.get("value") or o.get("title")
            except Exception:
                continue
    
    # Fallback to parsing title
    if variant.get("title"):
        t = variant.get("title")
        return t.split(" / ")[0] if " / " in t else t
    
    return None


def _build_color_mappings(prod: dict) -> tuple[dict[int, str], dict[str, str]]:
    """Build mappings: variant_id -> color_title and normalized_color -> design_src."""
    variant_id_to_title: dict[int, str] = {}
    color_to_src: dict[str, str] = {}
    
    # Map Printify variant IDs to color titles
    for pv in (prod.get("variants") or []):
        try:
            pvid = int(pv.get("id"))
            color = _extract_color_from_variant(pv)
            if color:
                variant_id_to_title[pvid] = str(color).strip()
        except Exception:
            continue
    
    # Map normalized colors to design sources from print areas
    for pa in (prod.get("print_areas") or []):
        pa_src = _get_front_src_from_print_area(pa)
        if not pa_src:
            continue
        
        for v in (pa.get("variant_ids") or []):
            try:
                v_int = int(v)
                color_title = variant_id_to_title.get(v_int)
                if color_title:
                    color_to_src[_normalize_str(color_title)] = pa_src
            except Exception:
                continue
    
    return variant_id_to_title, color_to_src


def _load_template_files(templates_dir: Path) -> list[str]:
    """Load template files from directory."""
    if not templates_dir.exists():
        raise FileNotFoundError(f"Templates folder missing: {templates_dir}")
    
    templates = [
        str(p) for p in sorted(templates_dir.iterdir())
        if p.suffix.lower() in Config.ALLOWED_EXTS
    ]
    
    if not templates:
        raise ValueError("No template images found in templates directory")
    
    return templates


def _load_colors_catalog(colors_file: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Load colors.json catalog mapping template names to hex codes."""
    template_hex_map: dict[str, str] = {}
    hex_to_template_names: dict[str, list[str]] = {}
    
    try:
        import json as _json
        if colors_file.exists():
            colors_data = _json.loads(colors_file.read_text(encoding="utf-8"))
            for entry in (colors_data or []):
                title = entry.get("Color") or entry.get("color")
                hexv = entry.get("Hex") or entry.get("hex")
                if not title or not hexv:
                    continue
                
                norm_title = _normalize_str(str(title))
                norm_hex = str(hexv).lstrip("#").upper()
                template_hex_map[norm_title] = norm_hex
                hex_to_template_names.setdefault(norm_hex, []).append(norm_title)
    except Exception:
        pass  # Non-fatal
    
    return template_hex_map, hex_to_template_names


def _get_shopify_variants(product_id: str) -> list[dict]:
    """Get Shopify product variants (from cache or API)."""
    try:
        shop_product = (
            store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or
            store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id)
        )
        if not shop_product:
            shop_product = shopify.get_product(product_id)
    except Exception:
        current_app.logger.exception("Failed to load Shopify product")
        shop_product = (
            store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or
            store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or
            {}
        )
    
    return shop_product.get("variants") or []


def _filter_templates_by_variants(templates: list[str], variants: list[dict]) -> list[str]:
    """Filter templates to only those matching enabled variant colors."""
    # Build map of template stems
    template_map: dict[str, str] = {}
    for t in templates:
        stem = Path(t).stem
        template_map[_normalize_str(stem)] = t
    
    # Collect used color titles from enabled variants
    used_titles: set[str] = set()
    for var in variants:
        try:
            if var.get("is_enabled", True) is False:
                continue
            
            color = _extract_color_from_variant(var)
            if color:
                used_titles.add(_normalize_str(color))
        except Exception:
            continue
    
    # Match templates to used colors
    matched_templates = []
    for norm_title in used_titles:
        if norm_title in template_map:
            matched_templates.append(template_map[norm_title])
    
    # Fallback to all templates if no matches
    if not matched_templates:
        current_app.logger.info("No template names matched Shopify variant colors; using all templates")
        return templates.copy()
    
    return matched_templates


def _download_design_to_tmp(src: str, product_id: str, stem: str) -> str:
    """Download design source to temp directory. Returns empty string on failure."""
    try:
        if str(src).startswith("/designs/"):
            p = Path("." + str(src))
            return str(p) if p.exists() else ""
        
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


def _find_design_for_template(
    template_path: str,
    color_to_src: dict[str, str],
    template_hex_map: dict[str, str],
    pa_bg_map: dict[str, str],
    fallback_src: str | None = None
) -> str | None:
    """Find the appropriate design image source for a template."""
    stem = Path(template_path).stem
    norm_stem = _normalize_str(stem)
    
    # 1. Direct color match
    design_src = color_to_src.get(norm_stem)
    if design_src:
        return design_src
    
    # 2. Hex color match via colors.json
    tmpl_hex = template_hex_map.get(norm_stem)
    if tmpl_hex and tmpl_hex in pa_bg_map:
        return pa_bg_map.get(tmpl_hex)
    
    # 3. Fuzzy matching
    try:
        import difflib
        candidates = list(color_to_src.keys())
        if candidates:
            matches = difflib.get_close_matches(norm_stem, candidates, n=1, cutoff=0.7)
            if matches:
                return color_to_src.get(matches[0])
    except Exception:
        pass
    
    # 4. Cross-match via hex codes
    if template_hex_map and tmpl_hex:
        for color_norm, src in color_to_src.items():
            color_hex = template_hex_map.get(color_norm)
            if color_hex and color_hex == tmpl_hex:
                return src
    
    # 5. Fallback
    return fallback_src


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
    # Send tags as an array of strings to Shopify (avoid storing a single comma string)
    # Ensure each tag is a clean string
    payload["tags"] = [str(t).strip() for t in (tags or []) if str(t).strip()]
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
      - Find associated Printify product
      - Extract design image source
      - Match templates to product variants by color
      - Generate mockups for each matched template
      - Return paths and variant mappings
    """
    # 1. Find associated Printify product
    pf = _find_printify_product_by_shopify_id(product_id)
    if not pf:
        return jsonify({"error": "No associated Printify product found in cache"}), 404

    printify_id = str(pf.get("id") or pf.get("_id"))
    
    # 2. Fetch Printify product details
    try:
        prod = printify.get_product(printify_id)
    except Exception as e:
        current_app.logger.exception("Failed to fetch Printify product %s", printify_id)
        return jsonify({"error": f"Failed to fetch Printify product: {e}"}), 400

    # 3. Extract front design image source
    src = _extract_front_design_src(prod)
    if not src:
        return jsonify({"error": "Could not find a front design image on Printify product"}), 404

    # 4. Resolve design to local path (download if needed)
    try:
        design_local_path = _resolve_design_path(src, product_id)
    except Exception as e:
        current_app.logger.exception("Failed to obtain design image")
        return jsonify({"error": f"Failed to obtain design image: {e}"}), 400

    # 5. Load templates
    templates_dir = Config.ASSETS_DIR / "mockups" / "g64k"
    try:
        templates = _load_template_files(templates_dir)
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 500

    # 6. Build color mappings from Printify product
    _, color_to_src = _build_color_mappings(prod)

    # 7. Load colors catalog
    template_hex_map, _ = _load_colors_catalog(templates_dir / "colors.json")

    # 8. Build background color map from print areas
    pa_bg_map: dict[str, str] = {}
    for pa in (prod.get("print_areas") or []):
        bg = pa.get("background")
        pa_src = _get_front_src_from_print_area(pa)
        if pa_src and isinstance(bg, str) and bg:
            norm_bg = str(bg).lstrip("#").upper()
            pa_bg_map.setdefault(norm_bg, pa_src)

    # 9. Get Shopify variants and filter templates
    variants = _get_shopify_variants(product_id)
    templates_to_generate = _filter_templates_by_variants(templates, variants)

    # 10. Build variant -> color title mapping for final output
    variant_to_title: dict[int, str] = {}
    for var in variants:
        try:
            if var.get("is_enabled", True) is False:
                continue
            vid = int(var.get("id") or 0)
            color = _extract_color_from_variant(var)
            if color:
                variant_to_title[vid] = str(color).strip()
        except Exception:
            continue

    # 11. Generate mockups for each template
    out_dir = Config.ASSETS_DIR / "product_mockups" / str(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    out_files = []
    for template_path in templates_to_generate:
        stem = Path(template_path).stem
        
        # Find best design source for this template
        design_src = _find_design_for_template(
            template_path, color_to_src, template_hex_map, pa_bg_map,
            fallback_src=src
        )
        
        # Download/resolve design path
        template_design_local = None
        if design_src:
            template_design_local = _download_design_to_tmp(design_src, product_id, stem)
        
        if not template_design_local:
            template_design_local = design_local_path
        
        if not template_design_local:
            current_app.logger.warning("No design available for template %s; skipping", stem)
            continue

        # Generate mockup
        try:
            generate_mockups_for_design(
                design_png_path=template_design_local,
                templates=[template_path],
                placements={},
                out_dir=out_dir,
                scale=1.0,
            )
        except Exception as e:
            current_app.logger.exception("Mockup generation failed for template %s", stem)
            continue

        # Rename and collect output
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

    # 12. Build output: relative paths and variant mappings
    rel_out = [str(p.relative_to(Config.BASE_DIR)) for p in sorted(out_files)]
    
    stem_to_relpath: dict[str, str] = {}
    for p in out_files:
        stem = _normalize_str(Path(p).stem)
        try:
            stem_to_relpath[stem] = str(p.relative_to(Config.BASE_DIR))
        except Exception:
            stem_to_relpath[stem] = str(p)

    variants_to_update: dict[int, str] = {}
    for vid, title in variant_to_title.items():
        norm_title = _normalize_str(title)
        if norm_title in stem_to_relpath:
            variants_to_update[vid] = stem_to_relpath[norm_title]

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
    refreshed = None
    try:
        # shopify.get_product may not exist on all client implementations; guard it
        if hasattr(shopify, 'get_product') and callable(getattr(shopify, 'get_product')):
            refreshed = shopify.get_product(product_id)
            if refreshed:
                store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
        else:
            current_app.logger.info("Shopify client has no get_product method; skipping refresh")
    except Exception:
        current_app.logger.exception("Failed to refresh Shopify product after image update")

    resp = {
        "ok": True,
        "uploaded": uploaded_images,
        "errors": errors,
        "updated_product": (refreshed if refreshed is not None else updated)
    }
    return jsonify(resp)
