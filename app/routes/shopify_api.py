from flask import Blueprint, request, jsonify, current_app

from ..extensions import store, shopify_client as shopify
from .. import Config
from ..extensions import printify_client as printify
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import httpx
from threading import Thread
from urllib.parse import urlparse
from werkzeug.utils import secure_filename

from ..utils.mockups import generate_mockups_for_design
from ..services.openai_svc import suggest_description, suggest_lifestyle_prompt
from ..services.gemini_svc import generate_lifestyle_images

bp = Blueprint("shopify_api", __name__)

SHOPIFY_PRODUCTS_COLLECTION = "shopify_products"
UPDATE_PROGRESS: dict[str, dict] = {}


def _normalize_product_tags(product: dict) -> dict:
    """Normalize tags from comma-separated string to array format for database storage."""
    if product and "tags" in product:
        raw_tags = product["tags"]
        if isinstance(raw_tags, str):
            product["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif not isinstance(raw_tags, list):
            product["tags"] = []
    return product


def _merge_swatch_mapping_status(product: dict, status: dict | None) -> dict:
    """Persist local swatch mapping status in cached product records."""
    merged = dict(product or {})
    if not status:
        return merged
    merged["swatch_mapping"] = status
    return merged


def _persona_key_to_local_path(person_key: str | None) -> str | None:
    key = str(person_key or "").strip()
    if not key.startswith("persona:"):
        return None
    filename = key.split("persona:", 1)[1]
    p = Config.ASSETS_DIR / "personas" / filename
    if p.exists() and p.is_file():
        return str(p)
    return None


def _safe_slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    out = "-".join(part for part in out.split("-") if part)
    return out or "unknown"


def _lifestyle_root(product_id: str) -> Path:
    return Config.DATA_DIR / "designs" / f"shopify-{product_id}" / "lifestyle"


def _migrate_lifestyle_assets_to_data(product_id: str) -> None:
    old_root = Config.ASSETS_DIR / "lifestyle" / str(product_id)
    new_root = _lifestyle_root(product_id)
    if not old_root.exists():
        return
    new_root.mkdir(parents=True, exist_ok=True)
    for src in old_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(old_root)
        dst = new_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            continue
        try:
            shutil.move(str(src), str(dst))
        except Exception:
            continue


def _resolve_printify_reference_image(
    product_id: str,
    garment_color: str,
    print_location: str,
) -> tuple[str, str]:
    """Ensure local cached Printify reference image for this product/color/location.

    Returns: (local_path, source_url)
    """
    color_slug = _safe_slug(garment_color)
    loc = (print_location or "front").strip().lower()
    _migrate_lifestyle_assets_to_data(product_id)
    refs_dir = _lifestyle_root(product_id) / "printify_refs"
    refs_dir.mkdir(parents=True, exist_ok=True)

    # Reuse previously downloaded reference if present.
    existing = sorted(refs_dir.glob(f"{loc}_{color_slug}.*"))
    if existing:
        return str(existing[0]), ""

    pf = _find_printify_product_by_shopify_id(product_id)
    if not pf:
        raise FileNotFoundError("No linked Printify product found for this Shopify product.")
    printify_id = str(pf.get("id") or pf.get("_id") or "")
    if not printify_id:
        raise FileNotFoundError("Linked Printify product id missing in cache.")

    prod = printify.get_product(printify_id)
    target_color = _normalize_str(garment_color)

    # Build color option-id lookup and variant ids for the selected color.
    color_id_by_title: dict[str, int] = {}
    for opt in (prod.get("options") or []):
        name = _normalize_str(opt.get("name") or opt.get("type"))
        if name not in ("color", "colour"):
            continue
        for v in (opt.get("values") or []):
            try:
                vid = int(v.get("id"))
            except Exception:
                continue
            title = _normalize_str(v.get("title") or v.get("name") or "")
            if title:
                color_id_by_title[title] = vid

    target_color_id = color_id_by_title.get(target_color)
    color_variant_ids: set[int] = set()
    for var in (prod.get("variants") or []):
        try:
            pid = int(var.get("id"))
        except Exception:
            continue
        opts = var.get("options")
        if isinstance(opts, list):
            if target_color_id is not None and int(target_color_id) in [int(x) for x in opts if str(x).isdigit()]:
                color_variant_ids.add(pid)
                continue
            title = _normalize_str(var.get("title"))
            if target_color and target_color in title:
                color_variant_ids.add(pid)
                continue
        elif isinstance(opts, dict):
            c = _normalize_str(opts.get("color") or opts.get("Color") or opts.get("colour") or opts.get("Colour"))
            if c == target_color:
                color_variant_ids.add(pid)

    # Rank product image candidates.
    best_url = None
    best_score = -999
    for im in (prod.get("images") or []):
        if isinstance(im, str):
            src = im
            variant_ids = []
            position = ""
            is_default = False
        else:
            src = im.get("src") or im.get("url")
            variant_ids = [int(v) for v in (im.get("variant_ids") or []) if str(v).isdigit()]
            position = _normalize_str(im.get("position"))
            is_default = bool(im.get("is_default"))
        if not src:
            continue

        score = 0
        if loc and position:
            score += 6 if position == loc else -2
        if color_variant_ids:
            if variant_ids and set(variant_ids).intersection(color_variant_ids):
                score += 8
            elif variant_ids:
                score -= 3
        if is_default:
            score += 1
        if score > best_score:
            best_score = score
            best_url = src

    if not best_url:
        best_url = prod.get("preview")
    if not best_url:
        raise FileNotFoundError("Could not find a usable Printify reference image URL.")

    with httpx.Client(timeout=60) as client:
        r = client.get(best_url)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        else:
            path_ext = Path(urlparse(best_url).path).suffix.lower()
            if path_ext in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".jpg" if path_ext == ".jpeg" else path_ext
        out_file = refs_dir / f"{loc}_{color_slug}{ext}"
        out_file.write_bytes(r.content)

    return str(out_file), best_url


def _lifestyle_local_path_from_url(product_id: str, url: str) -> Path | None:
    if not isinstance(url, str):
        return None

    # New path: /designs/shopify-<id>/lifestyle/<file>
    new_prefix = f"/designs/shopify-{product_id}/lifestyle/"
    if url.startswith(new_prefix):
        rel = url[len(new_prefix):].strip()
        base = _lifestyle_root(product_id).resolve()
        p = (base / rel).resolve()
        if str(p).startswith(str(base)):
            return p

    # Legacy path: /assets/lifestyle/<id>/<file>
    old_prefix = "/assets/"
    if url.startswith(old_prefix):
        rel = url[len(old_prefix):].strip()
        if rel.startswith(f"lifestyle/{product_id}/"):
            p = (Config.ASSETS_DIR / rel).resolve()
            old_base = (Config.ASSETS_DIR / "lifestyle" / str(product_id)).resolve()
            if str(p).startswith(str(old_base)):
                return p
    return None


# ========================================
# Helper functions for generate_mockups
# ========================================

def _normalize_str(s: str) -> str:
    """Normalize string for color/title matching."""
    return (s or "").strip().lower()


def _preferred_color_order(shop_product: dict) -> list[str]:
    """Return ordered color values from Shopify product options (if present)."""
    for opt in (shop_product.get("options") or []):
        try:
            name = (opt.get("name") or "").strip().lower()
            opt_type = (opt.get("type") or "").strip().lower()
            if name in ("color", "colour") or opt_type == "color":
                values = opt.get("values") or []
                ordered = []
                for v in values:
                    if isinstance(v, dict):
                        val = v.get("name") or v.get("value") or v.get("title")
                    else:
                        val = v
                    if val:
                        ordered.append(str(val).strip())
                return ordered
        except Exception:
            continue
    return []


def _find_printify_product_by_shopify_id(product_id: str) -> dict | None:
    """Find Printify product associated with Shopify product ID.

    Resolution order:
    1) local printify_products cache by shopify_product_id
    2) designs integrations mapping
    3) live Printify product scan (and backfill cache)
    """
    product_id = str(product_id)
    for item in store.list("printify_products"):
        if str(item.get("shopify_product_id") or "") == product_id:
            return item

    # Fallback 1: designs integrations mapping
    for design in store.list("designs"):
        integ = (design.get("integrations") or {}).get("printify_product") or {}
        if str(integ.get("shopify_product_id") or "") == product_id:
            pid = str(integ.get("id") or integ.get("_id") or "")
            if pid:
                # Return existing cached item by id if present, otherwise minimal handle.
                by_id = store.get("printify_products", pid)
                if by_id:
                    return by_id
                return {"id": pid, "shopify_product_id": product_id, "title": design.get("title") or ""}

    # Fallback 2: scan live Printify products to find external.shopify_product_id
    def _extract_shopify_external_id(prod: dict) -> str | None:
        ext = prod.get("external") or {}
        if isinstance(ext, dict):
            candidate = ext.get("id") or ext.get("product_id")
            if candidate:
                return str(candidate)
        elif isinstance(ext, str) and ext.isdigit():
            return ext
        return None

    page = 1
    while page <= 30:
        try:
            data = printify.list_products(page=page, limit=50)
        except Exception:
            break
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            break
        for prod in items:
            sid = _extract_shopify_external_id(prod)
            if sid != product_id:
                continue
            pid = str(prod.get("id") or prod.get("_id") or "")
            if not pid:
                continue
            cached = {
                "id": pid,
                "title": prod.get("title") or prod.get("name") or "",
                "shopify_product_id": product_id,
                "created_at": prod.get("created_at"),
                "updated_at": prod.get("updated_at"),
            }
            try:
                store.upsert("printify_products", pid, cached)
            except Exception:
                pass
            return cached
        current_page = int(data.get("current_page") or page)
        last_page = int(data.get("last_page") or current_page)
        if current_page >= last_page:
            break
        page += 1
    return None


def _generate_shopify_mockups_for_product(product_id: str, placements: dict, scale: float = 1.0) -> list[Path]:
    """Generate mockups for a Shopify product using Printify color->design mapping."""
    pf = _find_printify_product_by_shopify_id(product_id)
    if not pf:
        raise FileNotFoundError("No associated Printify product found in cache")

    printify_id = str(pf.get("id") or pf.get("_id"))
    prod = printify.get_product(printify_id)

    src = _extract_front_design_src(prod)
    if not src:
        raise FileNotFoundError("Could not find a front design image on Printify product")

    design_local_path = _resolve_design_path(src, product_id)

    # Persist a local design record for placement editing
    try:
        slug = f"shopify-{product_id}"
        design_dir = Config.DATA_DIR / "designs" / slug
        design_dir.mkdir(parents=True, exist_ok=True)
        src_path = Path(design_local_path)
        ext = src_path.suffix if src_path.suffix else ".png"
        dest_path = design_dir / f"printify_front{ext}"
        if not dest_path.exists() or dest_path.stat().st_size == 0:
            shutil.copyfile(src_path, dest_path)

        existing = store.get("designs", slug) or {}
        design_record = {
            "slug": slug,
            "title": prod.get("title") or f"Shopify {product_id}",
            "design_png_path": str(dest_path.relative_to(Config.BASE_DIR)),
            "collections": existing.get("collections", []),
            "tags": existing.get("tags", []),
            "notes": existing.get("notes", ""),
            "status": existing.get("status", {
                "mockups_generated": False,
                "product_created_printify": False,
                "published_shopify": False,
            }),
            "generated": existing.get("generated", {
                "title": None,
                "description": None,
                "keywords": [],
                "colors": [],
            }),
            "metadata": existing.get("metadata", {}),
        }
        design_record.setdefault("integrations", {})["printify_product"] = {
            "id": str(prod.get("id") or prod.get("_id") or ""),
            "shopify_product_id": str(product_id),
        }
        store.upsert("designs", slug, design_record)
    except Exception:
        current_app.logger.exception("Failed to persist design record for Shopify %s", product_id)

    templates_dir = Config.MOCKUP_STYLE_G64K_DIR
    templates = _load_template_files(templates_dir)
    variants = _get_shopify_variants(product_id)
    templates_to_generate = _filter_templates_by_variants(templates, variants)

    _, color_to_src = _build_color_mappings(prod)
    template_hex_map, _ = _load_colors_catalog(templates_dir / "colors.json")

    pa_bg_map: dict[str, str] = {}
    for pa in (prod.get("print_areas") or []):
        bg = pa.get("background")
        pa_src = _get_front_src_from_print_area(pa)
        if pa_src and isinstance(bg, str) and bg:
            pa_bg_map.setdefault(str(bg).lstrip("#").upper(), pa_src)

    out_dir = _product_mockups_dir(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_files = []
    for template_path in templates_to_generate:
        stem = Path(template_path).stem
        design_src = _find_design_for_template(
            template_path, color_to_src, template_hex_map, pa_bg_map, fallback_src=src
        )
        template_design_local = None
        if design_src:
            template_design_local = _download_design_to_tmp(design_src, product_id, stem)
        if not template_design_local:
            template_design_local = design_local_path
        if not template_design_local:
            continue

        generate_mockups_for_design(
            design_png_path=template_design_local,
            templates=[template_path],
            placements=placements,
            out_dir=out_dir,
            scale=scale,
        )

        gen_path = out_dir / f"mockup_{stem}.png"
        final_name = out_dir / f"{stem}.png"
        try:
            if gen_path.exists():
                if final_name.exists():
                    final_name.unlink(missing_ok=True)
                gen_path.replace(final_name)
                out_files.append(final_name)
            elif final_name.exists():
                out_files.append(final_name)
        except Exception:
            if final_name.exists():
                out_files.append(final_name)

    return out_files


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


def _product_mockups_dir(product_id: str) -> Path:
    """Canonical product mockup directory with migration from legacy locations."""
    new_dir = Config.PRODUCT_MOCKUPS_DIR / f"shopify-{product_id}" / "mockups"
    legacy_dirs = [
        Config.ASSETS_DIR / "product_mockups" / str(product_id),
        Config.DATA_DIR / "product_mockups" / str(product_id),
    ]
    for old_dir in legacy_dirs:
        if not old_dir.exists():
            continue
        new_dir.mkdir(parents=True, exist_ok=True)
        for src in old_dir.iterdir():
            if not src.is_file() or src.suffix.lower() not in Config.ALLOWED_EXTS:
                continue
            dst = new_dir / src.name
            if dst.exists():
                continue
            try:
                shutil.copy2(src, dst)
            except Exception:
                continue
    return new_dir


def _humanize_color_stem(value: str) -> str:
    parts = [p for p in str(value or "").replace("_", " ").replace("-", " ").split() if p]
    if not parts:
        return "mockup"
    return " ".join(p.capitalize() for p in parts)


def _choose_manual_mockup_stem(raw_stem: str, preferred_colors: list[str]) -> str:
    norm_raw = _normalize_str(raw_stem)
    if not norm_raw:
        return "mockup"

    by_norm = {}
    for c in preferred_colors or []:
        text = str(c or "").strip()
        if text:
            by_norm[_normalize_str(text)] = text

    if norm_raw in by_norm:
        return by_norm[norm_raw]

    try:
        import difflib
        match = difflib.get_close_matches(norm_raw, list(by_norm.keys()), n=1, cutoff=0.7)
        if match:
            return by_norm[match[0]]
    except Exception:
        pass

    return _humanize_color_stem(raw_stem)


# -----------------------------
# Shopify: upload images + cache product list
# -----------------------------
@bp.post("/shopify/products/<product_id>/manual_mockups")
def api_shopify_upload_manual_mockups(product_id: str):
    def _rel_or_abs(path: Path) -> str:
        try:
            return str(path.relative_to(Config.BASE_DIR))
        except Exception:
            return str(path)

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded. Use multipart/form-data with one or more 'files' fields."}), 400

    replace_existing = str(request.form.get("replace_existing", "true")).strip().lower() in ("1", "true", "yes", "on")
    out_dir = _product_mockups_dir(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    if replace_existing:
        for p in out_dir.iterdir():
            if p.is_file() and p.suffix.lower() in Config.ALLOWED_EXTS:
                p.unlink(missing_ok=True)

    preferred_colors = []
    variants = _get_shopify_variants(product_id)
    for v in variants:
        c = _extract_color_from_variant(v)
        if c:
            c = str(c).strip()
            if c and c not in preferred_colors:
                preferred_colors.append(c)

    saved = []
    rejected = []
    for f in files:
        original_name = secure_filename(f.filename or "")
        stem = Path(original_name).stem
        ext = Path(original_name).suffix.lower()

        if not ext and f.mimetype:
            if "png" in f.mimetype:
                ext = ".png"
            elif "jpeg" in f.mimetype or "jpg" in f.mimetype:
                ext = ".jpg"
            elif "webp" in f.mimetype:
                ext = ".webp"

        if ext not in Config.ALLOWED_EXTS:
            rejected.append({"file": f.filename or "", "error": f"Unsupported extension '{ext or '(none)'}'"})
            continue

        target_stem = _choose_manual_mockup_stem(stem, preferred_colors)
        target = out_dir / f"{target_stem}{ext}"
        f.save(target)
        saved.append({
            "original": f.filename,
            "saved_as": _rel_or_abs(target),
            "matched_color": target_stem if target_stem in preferred_colors else None,
        })

    if not saved:
        return jsonify({"error": "No valid files uploaded", "rejected": rejected}), 400

    return jsonify({
        "ok": True,
        "saved": saved,
        "rejected": rejected,
        "saved_count": len(saved),
        "folder": _rel_or_abs(out_dir),
        "replace_existing": replace_existing,
    })


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
        existing = (store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or
                    store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or {})
        existing_status = existing.get("swatch_mapping")
        product = shopify.get_product(product_id)
        if product:
            # Normalize tags from comma-separated string to array for database storage
            product = _normalize_product_tags(product)
            product = _merge_swatch_mapping_status(product, existing_status)
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), product)
            return jsonify({"ok": True, "product": product})
        return jsonify({"error": "Product not found"}), 404
    except Exception as e:
        current_app.logger.exception("Shopify refresh failed")
        return jsonify({"error": str(e)}), 400


@bp.post("/shopify/products/<product_id>/apply_swatches")
def api_shopify_apply_swatches(product_id: str):
    """Apply Shopify swatches for Color/Colour option values."""
    if shopify is None or not hasattr(shopify, "apply_color_swatches"):
        return jsonify({"error": "Shopify swatch update is unavailable"}), 501

    try:
        result = shopify.apply_color_swatches(product_id)
    except Exception as e:
        msg = str(e)
        if "OptionValueUpdateInput" in msg and "swatch" in msg.lower():
            return jsonify({
                "error": (
                    "This Shopify API version doesn't allow setting swatches directly on "
                    "optionValuesToUpdate. Swatches must be applied through metafield-linked "
                    "color options (shopify.color-pattern)."
                ),
                "details": msg,
            }), 400
        if "Access denied" in msg and "metaobjects" in msg.lower():
            return jsonify({
                "error": (
                    "Shopify access token is missing metaobject read access. "
                    "Enable the `read_metaobjects` scope for this app and reinstall/update the app token, "
                    "then try again."
                ),
                "details": msg,
            }), 403
        current_app.logger.exception("Failed to apply swatches for Shopify product %s", product_id)
        return jsonify({"error": msg}), 500

    refreshed = None
    try:
        status = {
            "updated": int(result.get("updated") or 0),
            "total_color_values": int(result.get("total_color_values") or 0),
            "linked_after": int(result.get("linked_after") or 0),
            "skipped_no_pattern": result.get("skipped_no_pattern") or [],
            "skipped_unmatched": result.get("skipped_unmatched") or [],
            "skipped_already_linked": result.get("skipped_already_linked") or [],
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
        }
        if status["total_color_values"] == 0:
            status["state"] = "no_color_option"
        elif status["linked_after"] >= status["total_color_values"]:
            status["state"] = "mapped"
        else:
            status["state"] = "needs_mapping"
        status["needs_mapping"] = status["state"] in ("needs_mapping",)
        refreshed = shopify.get_product(product_id)
        if refreshed:
            refreshed = _merge_swatch_mapping_status(refreshed, status)
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
        else:
            existing = (store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or
                        store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or {})
            if existing:
                store.upsert(
                    SHOPIFY_PRODUCTS_COLLECTION,
                    str(product_id),
                    _merge_swatch_mapping_status(existing, status),
                )
    except Exception:
        current_app.logger.exception("Failed to refresh Shopify product after swatch apply")

    return jsonify({
        "ok": True,
        "product_id": str(product_id),
        **result,
        "product": refreshed if refreshed is not None else {},
    })


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
    # Generate using shared helper (keeps placement + color mapping consistent)
    try:
        out_files = _generate_shopify_mockups_for_product(product_id, placements={}, scale=1.0)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        current_app.logger.exception("Mockup generation failed for product %s", product_id)
        return jsonify({"error": f"Mockup generation failed: {e}"}), 500

    variants = _get_shopify_variants(product_id)
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

    # 12. Build output: relative paths and variant mappings
    rel_out = []
    for p in sorted(out_files):
        path = p if p.is_absolute() else (Config.BASE_DIR / p).resolve()
        try:
            rel_out.append(str(path.relative_to(Config.BASE_DIR)))
        except Exception:
            rel_out.append(str(p))
    
    stem_to_relpath: dict[str, str] = {}
    for p in out_files:
        stem = _normalize_str(Path(p).stem)
        path = p if p.is_absolute() else (Config.BASE_DIR / p).resolve()
        try:
            stem_to_relpath[stem] = str(path.relative_to(Config.BASE_DIR))
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
    folder = _product_mockups_dir(product_id)
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
    files_to_upload: dict[str, list[int]] = {}
    color_to_path: dict[str, str] = {}
    for vid, rel in variants_to_file.items():
        # resolve absolute path
        p = Path(rel)
        if not p.is_absolute():
            p = Config.BASE_DIR / rel
        path_str = str(p)
        files_to_upload.setdefault(path_str, []).append(int(vid))
        # remember color->path for ordering
        title = variant_to_title_local.get(vid)
        if title:
            color_to_path[_norm_local(title)] = path_str

    # Build ordered upload plan based on Shopify color option order
    ordered_paths: list[str] = []
    seen_paths: set[str] = set()
    preferred = _preferred_color_order(shop_product)
    if preferred:
        for color in preferred:
            pth = color_to_path.get(_norm_local(color))
            if pth and pth not in seen_paths:
                ordered_paths.append(pth)
                seen_paths.add(pth)
    # Append any remaining paths not already ordered (stable, sorted for determinism)
    for pth in sorted(files_to_upload.keys()):
        if pth not in seen_paths:
            ordered_paths.append(pth)
            seen_paths.add(pth)

    uploaded_images = []
    path_to_image_id: dict[str, int] = {}
    errors = []

    for path_str in ordered_paths:
        vids = files_to_upload.get(path_str, [])
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

    # Prefer the first color in Shopify's option order if available
    if not default_image_id:
        preferred = _preferred_color_order(shop_product)
        if preferred:
            for color in preferred:
                n = _norm_local(color)
                for vid, title in variant_to_title_local.items():
                    if _norm_local(title) == n and vid in variants_to_file:
                        p_rel = variants_to_file[vid]
                        p_abs = Config.BASE_DIR / p_rel if not Path(p_rel).is_absolute() else Path(p_rel)
                        default_image_id = path_to_image_id.get(str(p_abs))
                        break
                if default_image_id:
                    break

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


@bp.post("/shopify/products/<product_id>/update_mockups")
def api_shopify_update_mockups(product_id: str):
    """Replace variant-linked Shopify images using generated mockups on disk."""
    if UPDATE_PROGRESS.get(str(product_id), {}).get("running"):
        return jsonify({"error": "Update already in progress"}), 409

    UPDATE_PROGRESS[str(product_id)] = {
        "running": True,
        "phase": "starting",
        "total": 0,
        "completed": 0,
        "uploaded": 0,
        "deleted": 0,
        "done": False,
        "error": None,
    }

    def _worker():
        try:
            progress = UPDATE_PROGRESS[str(product_id)]

            # 1) Locate generated mockups
            folder = _product_mockups_dir(product_id)
            if not folder.exists():
                raise RuntimeError(f"No generated mockups folder found for product {product_id}")

            files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in Config.ALLOWED_EXTS]
            if not files:
                raise RuntimeError("No generated mockup image files found")

            # 2) Load Shopify product (cache then live)
            try:
                shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id)
                if not shop_product:
                    shop_product = shopify.get_product(product_id)
            except Exception:
                current_app.logger.exception("Failed to load Shopify product for update_mockups")
                shop_product = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or store.get(SHOPIFY_PRODUCTS_COLLECTION, product_id) or {}

            if not shop_product:
                raise RuntimeError("Shopify product not found (cache or API)")

            # 3) Map file stems -> image data
            color_image_map: dict[str, Path] = {}
            for p in files:
                color_image_map[_normalize_str(p.stem)] = p

            # 4) Build variant color map
            variant_to_color: dict[int, str] = {}
            for var in (shop_product.get("variants") or []):
                try:
                    if var.get("is_enabled", True) is False:
                        continue
                    vid = int(var.get("id") or 0)
                    color = None
                    if var.get("option1"):
                        color = var.get("option1")
                    elif var.get("option2"):
                        color = var.get("option2")
                    else:
                        t = var.get("title")
                        if t:
                            color = t.split(" / ")[0] if " / " in t else t
                    if color:
                        variant_to_color[vid] = str(color).strip()
                except Exception:
                    continue

            if not variant_to_color:
                raise RuntimeError("No Shopify variants found or could not resolve variant colors")

            # 5) Build color -> variant ids
            color_variant_ids: dict[str, list[int]] = {}
            for vid, color in variant_to_color.items():
                key = _normalize_str(color)
                if key in color_image_map:
                    color_variant_ids.setdefault(key, []).append(vid)

            if not color_variant_ids:
                raise RuntimeError("No matching variant colors found for mockup files")

            # 6) Delete old images attached to these variants
            progress["phase"] = "deleting"
            variant_ids_to_replace = {vid for ids in color_variant_ids.values() for vid in ids}
            images_to_delete = []
            for img in (shop_product.get("images") or []):
                img_variant_ids = img.get("variant_ids") or []
                if any(int(v) in variant_ids_to_replace for v in img_variant_ids):
                    if img.get("id"):
                        images_to_delete.append(int(img["id"]))

            progress["total"] = len(images_to_delete)
            progress["completed"] = 0

            with httpx.Client(timeout=60) as client:
                for image_id in images_to_delete:
                    try:
                        url = f"{shopify.base}/products/{product_id}/images/{image_id}.json"
                        r = client.delete(url, headers=shopify.headers)
                        if r.status_code not in (200, 204):
                            current_app.logger.warning("Failed to delete image %s: %s %s", image_id, r.status_code, r.text)
                        else:
                            progress["deleted"] += 1
                    except Exception:
                        current_app.logger.exception("Failed to delete image %s", image_id)
                    progress["completed"] += 1

            # 7) Upload new images (convert to webp in client)
            progress["phase"] = "uploading"
            progress["total"] = len(color_variant_ids)
            progress["completed"] = 0
            color_variant_image_id_map: dict[str, int] = {}
            uploaded = []
            errors = []
            # upload in preferred Shopify color order
            ordered_color_keys: list[str] = []
            seen_keys: set[str] = set()
            preferred = _preferred_color_order(shop_product)
            if preferred:
                for color in preferred:
                    key = _normalize_str(color)
                    if key in color_image_map and key in color_variant_ids and key not in seen_keys:
                        ordered_color_keys.append(key)
                        seen_keys.add(key)
            for key in sorted(color_image_map.keys()):
                if key in color_variant_ids and key not in seen_keys:
                    ordered_color_keys.append(key)
                    seen_keys.add(key)

            for color_key in ordered_color_keys:
                path = color_image_map.get(color_key)
                if not path:
                    continue
                try:
                    res = shopify.upload_product_images(product_id, [str(path)])
                    info = res[0] if res else None
                    img_obj = info.get("image") if isinstance(info, dict) and info.get("image") else info
                    image_id = img_obj.get("id") if isinstance(img_obj, dict) else None
                    if not image_id:
                        raise RuntimeError(f"No image id returned for {path}")
                    color_variant_image_id_map[color_key] = int(image_id)
                    uploaded.append({"color": color_key, "image_id": int(image_id)})
                    progress["uploaded"] += 1
                except Exception as e:
                    current_app.logger.exception("Failed to upload mockup %s", path)
                    errors.append({"file": str(path), "error": str(e)})
                progress["completed"] += 1

            if not uploaded:
                raise RuntimeError("No images were uploaded")

            # 8) Attach images to variant ids + set featured image
            progress["phase"] = "attaching"
            images_payload = []
            for color_key, image_id in color_variant_image_id_map.items():
                vids = color_variant_ids.get(color_key, [])
                if vids:
                    images_payload.append({"id": int(image_id), "variant_ids": vids})

            # determine featured image by preferred color order if available
            default_image_id = None
            preferred = _preferred_color_order(shop_product)
            if preferred:
                for color in preferred:
                    key = _normalize_str(color)
                    if key in color_variant_image_id_map:
                        default_image_id = color_variant_image_id_map[key]
                        break

            if not default_image_id:
                # fallback to first variant color in order
                for var in (shop_product.get("variants") or []):
                    try:
                        color = (var.get("option1") or var.get("option2") or var.get("title") or "").strip()
                        key = _normalize_str(color.split(" / ")[0] if " / " in color else color)
                        if key in color_variant_image_id_map:
                            default_image_id = color_variant_image_id_map[key]
                            break
                    except Exception:
                        continue
            if not default_image_id:
                default_image_id = uploaded[0].get("image_id")

            update_payload = {"images": images_payload}
            if default_image_id:
                update_payload["image"] = {"id": int(default_image_id)}

            try:
                updated = shopify.update_product(product_id, update_payload)
            except Exception as e:
                current_app.logger.exception("Failed to update Shopify product images for %s", product_id)
                raise RuntimeError(f"Failed to update Shopify product: {e}")

            try:
                if hasattr(shopify, 'get_product') and callable(getattr(shopify, 'get_product')):
                    refreshed = shopify.get_product(product_id)
                    if refreshed:
                        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
            except Exception:
                current_app.logger.exception("Failed to refresh Shopify product after update_mockups")

            progress["phase"] = "done"
            progress["done"] = True
            progress["running"] = False
        except Exception as e:
            progress = UPDATE_PROGRESS.get(str(product_id), {})
            progress["error"] = str(e)
            progress["phase"] = "error"
            progress["done"] = True
            progress["running"] = False
            UPDATE_PROGRESS[str(product_id)] = progress

    Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@bp.post("/shopify/products/<product_id>/ai/description")
def api_shopify_ai_description(product_id: str):
    body = request.get_json(silent=True) or {}
    title_hint = body.get("title") or ""
    tags = body.get("tags") or []
    notes = body.get("notes") or ""
    if not title_hint or not tags:
        cached = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
        title_hint = title_hint or cached.get("title") or ""
        if not tags:
            tags = cached.get("tags") or []
    try:
        description = suggest_description(title_hint=title_hint, tags=tags, notes=notes)
    except Exception as e:
        current_app.logger.exception("AI description failed for Shopify %s", product_id)
        return jsonify({"error": str(e)}), 500
    return jsonify({"description": description})


@bp.post("/shopify/products/<product_id>/lifestyle/prompt")
def api_shopify_lifestyle_prompt(product_id: str):
    body = request.get_json(silent=True) or {}
    cached = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
    title = (cached.get("title") or "").strip()
    description = (cached.get("description") or cached.get("body_html") or "").strip()
    garment_type = (body.get("garment_type") or cached.get("type") or "T-Shirt").strip()
    garment_color = (body.get("garment_color") or "").strip()
    print_location = (body.get("print_location") or "front").strip().lower()
    person_selection = (body.get("person_selection") or "generic_female").strip()
    age_segment = (body.get("age_segment") or "35-44").strip()
    art_direction = (body.get("art_direction") or "").strip()
    num_images = int(body.get("num_images") or 1)

    if not garment_color:
        return jsonify({"error": "garment_color is required"}), 400
    if print_location not in ("front", "back"):
        return jsonify({"error": "print_location must be front or back"}), 400

    person_label = {
        "generic_female": "Generic Female",
        "generic_male": "Generic Male",
    }.get(person_selection, person_selection.replace("persona:", "").replace("_", " "))

    try:
        prompt = suggest_lifestyle_prompt(
            title=title,
            description=description,
            garment_type=garment_type,
            garment_color=garment_color,
            print_location=print_location,
            person_selection=person_label,
            age_segment=age_segment,
            art_direction=art_direction,
            num_images=num_images,
        )
    except Exception as e:
        current_app.logger.exception("Lifestyle prompt generation failed for Shopify %s", product_id)
        return jsonify({"error": str(e)}), 500

    # Persist last-used lifestyle controls so page reload keeps user selections.
    existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
    if existing:
        existing["lifestyle_defaults"] = {
            "garment_type": garment_type,
            "garment_color": garment_color,
            "print_location": print_location,
            "person_selection": person_selection,
            "age_segment": age_segment,
            "art_direction": art_direction,
            "num_images": max(1, min(num_images, 10)),
        }
        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), existing)

    return jsonify({"prompt": prompt})


@bp.post("/shopify/products/<product_id>/lifestyle/generate")
def api_shopify_lifestyle_generate(product_id: str):
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    num_images = int(body.get("num_images") or 1)
    person_selection = (body.get("person_selection") or "").strip()
    garment_color = (body.get("garment_color") or "").strip()
    print_location = (body.get("print_location") or "front").strip().lower()
    garment_type = (body.get("garment_type") or "").strip()
    age_segment = (body.get("age_segment") or "").strip()
    art_direction = (body.get("art_direction") or "").strip()

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    if not garment_color:
        return jsonify({"error": "garment_color is required"}), 400

    local_refs = []
    persona_path = _persona_key_to_local_path(person_selection)
    if persona_path:
        local_refs.append(persona_path)

    try:
        printify_ref_local_path, printify_ref_source_url = _resolve_printify_reference_image(
            product_id=product_id,
            garment_color=garment_color,
            print_location=print_location,
        )
        local_refs.append(printify_ref_local_path)
    except Exception as e:
        current_app.logger.exception("Failed to resolve Printify reference image for Shopify %s", product_id)
        return jsonify({"error": f"Could not prepare Printify reference image: {e}"}), 500

    try:
        generated = generate_lifestyle_images(
            prompt,
            num_images=max(1, min(num_images, 10)),
            reference_local_paths=local_refs,
            reference_urls=[],
        )
    except Exception as e:
        current_app.logger.exception("Lifestyle image generation failed for Shopify %s", product_id)
        return jsonify({"error": str(e)}), 500

    _migrate_lifestyle_assets_to_data(product_id)
    out_dir = _lifestyle_root(product_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    saved = []
    image_meta = {
        "person_selection": person_selection,
        "garment_color": garment_color,
        "print_location": print_location,
        "garment_type": garment_type,
        "age_segment": age_segment,
        "art_direction": art_direction,
        "num_images": max(1, min(num_images, 10)),
    }
    for i, item in enumerate(generated, start=1):
        mime = (item.get("mime_type") or "image/png").lower()
        ext = ".png"
        if "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif "webp" in mime:
            ext = ".webp"
        name = f"lifestyle_{stamp}_{i}{ext}"
        target = out_dir / name
        target.write_bytes(item["bytes"])
        meta_path = target.with_suffix(".json")
        meta_path.write_text(
            json.dumps(
                {
                    "prompt": prompt,
                    "meta": image_meta,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        rel = target.relative_to(out_dir).as_posix()
        saved.append({
            "path": str(target),
            "url": f"/designs/shopify-{product_id}/lifestyle/{rel}",
            "name": target.name,
            "prompt": prompt,
            "meta": image_meta,
        })

    existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
    history = existing.get("lifestyle_images") or []
    history = history + [s["url"] for s in saved]
    if existing:
        existing["lifestyle_images"] = history[-100:]
        existing["lifestyle_defaults"] = {
            "garment_type": garment_type,
            "garment_color": garment_color,
            "print_location": print_location,
            "person_selection": person_selection,
            "age_segment": age_segment,
            "art_direction": art_direction,
            "num_images": max(1, min(num_images, 10)),
        }
        refs = existing.get("lifestyle_reference_images") or {}
        refs[f"{_safe_slug(garment_color)}:{print_location}"] = {
            "local_path": printify_ref_local_path,
            "source_url": printify_ref_source_url,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        existing["lifestyle_reference_images"] = refs
        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), existing)

    return jsonify({
        "ok": True,
        "images": saved,
        "printify_reference": {
            "local_path": printify_ref_local_path,
            "source_url": printify_ref_source_url,
        },
    })


@bp.post("/shopify/products/<product_id>/lifestyle/images/delete")
def api_shopify_lifestyle_delete_images(product_id: str):
    body = request.get_json(silent=True) or {}
    urls = body.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "urls list is required"}), 400

    deleted = []
    missing = []
    for u in urls:
        p = _lifestyle_local_path_from_url(product_id, str(u))
        if not p:
            continue
        if p.exists():
            try:
                p.unlink()
                meta_path = p.with_suffix(".json")
                if meta_path.exists():
                    meta_path.unlink()
                deleted.append(str(u))
            except Exception:
                current_app.logger.exception("Failed deleting lifestyle image %s", p)
        else:
            missing.append(str(u))

    existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
    if existing:
        history = existing.get("lifestyle_images") or []
        existing["lifestyle_images"] = [u for u in history if u not in deleted]
        store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), existing)

    return jsonify({"ok": True, "deleted": deleted, "missing": missing})


@bp.post("/shopify/products/<product_id>/lifestyle/images/apply_to_shopify")
def api_shopify_lifestyle_apply_to_shopify(product_id: str):
    body = request.get_json(silent=True) or {}
    urls = body.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return jsonify({"error": "urls list is required"}), 400

    local_files = []
    selected_paths = []
    for u in urls:
        p = _lifestyle_local_path_from_url(product_id, str(u))
        if not p or not p.exists():
            continue
        local_files.append(str(p))
        selected_paths.append(p)

    if not local_files:
        return jsonify({"error": "No valid local images selected"}), 400

    try:
        uploaded = shopify.upload_product_images(product_id, local_files, webp_quality=90)
    except Exception as e:
        current_app.logger.exception("Failed uploading lifestyle images to Shopify %s", product_id)
        return jsonify({"error": str(e)}), 500

    uploaded_ids = []
    for rec in uploaded:
        img = rec.get("image") if isinstance(rec, dict) else None
        if not img and isinstance(rec, dict):
            img = rec
        try:
            iid = int((img or {}).get("id"))
            uploaded_ids.append(iid)
        except Exception:
            continue

    try:
        if hasattr(shopify, "place_images_after_hero"):
            shopify.place_images_after_hero(product_id, uploaded_ids)
    except Exception:
        current_app.logger.exception("Failed reordering uploaded lifestyle images for Shopify %s", product_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    per_file_upload_ids = {}
    for idx, p in enumerate(selected_paths):
        if idx < len(uploaded_ids):
            per_file_upload_ids[str(p)] = uploaded_ids[idx]

    for p in selected_paths:
        meta_path = p.with_suffix(".json")
        meta_doc = {}
        if meta_path.exists():
            try:
                meta_doc = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta_doc = {}
        shopify_meta = meta_doc.get("shopify") or {}
        shopify_meta.update({
            "uploaded": True,
            "uploaded_at": now_iso,
            "image_id": per_file_upload_ids.get(str(p)),
        })
        meta_doc["shopify"] = shopify_meta
        try:
            meta_path.write_text(json.dumps(meta_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            current_app.logger.exception("Failed writing lifestyle metadata sidecar for %s", p)

    refreshed = None
    try:
        refreshed = shopify.get_product(product_id)
        if refreshed:
            existing = store.get(SHOPIFY_PRODUCTS_COLLECTION, str(product_id)) or {}
            swatch_status = existing.get("swatch_mapping")
            refreshed = _merge_swatch_mapping_status(refreshed, swatch_status)
            if existing.get("lifestyle_images"):
                refreshed["lifestyle_images"] = existing["lifestyle_images"]
            if existing.get("lifestyle_reference_images"):
                refreshed["lifestyle_reference_images"] = existing["lifestyle_reference_images"]
            store.upsert(SHOPIFY_PRODUCTS_COLLECTION, str(product_id), refreshed)
    except Exception:
        current_app.logger.exception("Failed refreshing Shopify product after lifestyle upload")

    return jsonify({
        "ok": True,
        "uploaded_count": len(uploaded),
        "uploaded": uploaded,
        "uploaded_ids": uploaded_ids,
        "uploaded_urls": urls,
        "product": refreshed if refreshed is not None else {},
    })


@bp.get("/shopify/products/<product_id>/update_mockups_progress")
def api_shopify_update_mockups_progress(product_id: str):
    progress = UPDATE_PROGRESS.get(str(product_id))
    if not progress:
        return jsonify({"running": False, "phase": "idle", "total": 0, "completed": 0, "done": False})
    return jsonify(progress)
