import os
from flask import Blueprint, render_template

from ..extensions import store, printify_client as printify

bp = Blueprint("printify_pages", __name__)

PRINTIFY_PRODUCTS_COLLECTION = "printify_products"


@bp.get("/printify")
def printify_page():
    items = store.list(PRINTIFY_PRODUCTS_COLLECTION)

    # show newest first if we have dates
    def _ts(p):
        return p.get("updated_at") or p.get("created_at") or ""

    items = sorted(items, key=_ts, reverse=True)
    return render_template("printify.html", items=items, store_domain=os.getenv("SHOPIFY_STORE_DOMAIN"))


@bp.get("/printify/edit/<product_id>")
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

    # Get all the colors for a template - now just what we use but ALL
    for opt in (full.get("options") or []):
        if opt.get("type") == "color":
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
    def _variant_color_tuple(var: dict) -> tuple[str, str]:
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
            if pth.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
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
        "dark_url": _first_url("dark") or dark_panel_image,

        # initial saved pills per panel (from existing product mapping)
        "initial_saved_light": [{"title": t, "hex": h} for h, t in
                                sorted(light_panel_colors.items(), key=lambda kv: kv[1].lower())],
        "initial_saved_dark": [{"title": t, "hex": h} for h, t in
                               sorted(dark_panel_colors.items(), key=lambda kv: kv[1].lower())],
    }
    return render_template("printify_edit.html", p=ctx)


@bp.get("/printify/new")
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
