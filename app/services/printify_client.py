import base64
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

PRINTIFY_API_BASE = "https://api.printify.com/v1"

class PrintifyClient:
    def __init__(self):
        import os

        load_dotenv()  # ensure .env is loaded
        self.api_token = os.getenv("PRINTIFY_API_TOKEN")
        self.shop_id = os.getenv("PRINTIFY_SHOP_ID")
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    # === ANCHOR: DUPLICATE_FROM_TEMPLATE ===
    def duplicate_from_template(self, template: dict, *, title: str, description: str, tags: list[str] | None = None):
        """
        Build a valid Create Product payload by reusing the template's blueprint, provider,
        variant IDs, prices and print_areas (images positions), while stripping read-only fields.
        """
        import re
        HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")
        OID24 = re.compile(r"^[0-9a-f]{24}$")

        FRONT_LOGO = {
            "id": "68faffc792143382282f3002",  # your store logo file id
            "x": 0.5,
            "y": 0.29419525065963065,
            "scale": 1.0,
            "angle": 0
        }

        def _to_int(v, default=0):
            try:
                # If it's "0.0" or 0.0, handle gracefully
                return int(round(float(v)))
            except Exception:
                return int(default)

        def _to_float(v, default=0.0):
            try:
                return float(v)
            except Exception:
                return float(default)

        def _slim_variants(variants: list[dict]) -> list[dict]:
            slim = []
            seen_default = False
            for idx, v in enumerate(variants or []):
                entry = {
                    "id": int(v["id"]),
                    "price": int(v.get("price", 0)),
                    "is_enabled": bool(v.get("is_enabled", True)),
                }
                is_def = bool(v.get("is_default", False))
                if is_def and not seen_default:
                    entry["is_default"] = True
                    seen_default = True
                elif idx == 0 and not seen_default:
                    entry["is_default"] = True
                    seen_default = True
                slim.append(entry)
            return slim

        def _image_id_from_template(img: dict) -> str | None:
            """
            Only use images that have a resolvable source URL (src/url).
            Built-in/template-only assets without a src are skipped.
            We always upload the URL to our media library and use the new id.
            """
            src = img.get("src") or img.get("url")
            if isinstance(src, str) and src.startswith("http"):
                up = self.upload_image_by_url(url=src, file_name=(img.get("name") or "art.png"))
                return up.get("id")
            # No src => skip this image entry entirely
            return None

        def _slim_placeholders(placeholders: list[dict], *, position: str) -> list[dict]:
            """
            - For NON-FRONT positions: keep placeholders if they have at least one image with a usable id;
              do NOT try to remove/optimize them — preserve angle/scale/x/y (coerced).
            - For FRONT: we'll still pass through, but later in SAVE we will overwrite front placements anyway.
            """
            out = []
            for ph in (placeholders or []):
                imgs_in = ph.get("images") or []
                imgs = []
                for img in imgs_in:
                    iid = _image_id_from_template(img)
                    if iid:
                        imgs.append({
                            "id": iid,
                            "x": _to_float(img.get("x", 0.5)),
                            "y": _to_float(img.get("y", 0.5)),
                            "scale": _to_float(img.get("scale", 1.0)),
                            "angle": _to_int(img.get("angle", 0)),
                        })
                if not imgs:
                    # skip completely empty placeholders (Printify validator dislikes empty image lists)
                    continue
                entry = {"position": ph.get("position") or position, "images": imgs}
                # keep decoration_method if present
                if isinstance(ph.get("decoration_method"), str):
                    entry["decoration_method"] = ph["decoration_method"]
                out.append(entry)
            return out

        def _slim_print_areas(print_areas: list[dict]) -> list[dict]:
            """
            Preserve ALL positions from the template, especially non-front (neck/label/back/sleeves), with their images.
            """
            slim = []
            for pa in (print_areas or []):
                pos_list = [ (ph.get("position") or "").lower() for ph in (pa.get("placeholders") or []) ]
                # If the area has no placeholders, skip it.
                if not pa.get("placeholders"):
                    continue

                placeholders = []
                # Build placeholders per original position
                for ph in (pa.get("placeholders") or []):
                    position = ph.get("position") or "front"
                    ph_list = _slim_placeholders([ph], position=position)
                    placeholders.extend(ph_list)

                if not placeholders:
                    continue

                entry = {
                    "variant_ids": [int(v) for v in (pa.get("variant_ids") or [])],
                    "placeholders": placeholders,
                }
                bg = pa.get("background")
                if isinstance(bg, str) and HEX6.match(bg):
                    entry["background"] = bg.upper()
                slim.append(entry)
            return slim

        payload = {
            "blueprint_id": template["blueprint_id"],
            "title": title,
            "description": description,
            "print_provider_id": template["print_provider_id"],
            "tags": tags or template.get("tags", []),
            "print_areas": _slim_print_areas(template.get("print_areas", [])),
            "variants": _slim_variants(template["variants"]),
            # DO NOT include: images (mockups), options (read-only), id/created_at/updated_at, sales_channel_properties
        }

        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products.json"
        import json, datetime
        from pathlib import Path

        # --- DEBUG DUMP START ---
        DEBUG_DIR = Path("data/debug")
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dump_path = DEBUG_DIR / f"printify_create_payload_{timestamp}.json"

        # Pretty JSON
        dump_data = {
            "endpoint": url,
            "headers": self.headers,
            "payload": payload,
        }
        dump_path.write_text(json.dumps(dump_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[DEBUG] Wrote full Printify payload to {dump_path}")
        # --- DEBUG DUMP END ---

        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers, json=payload)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(f"{e} — body: {r.text}", request=e.request, response=e.response)
            return r.json()

    def list_products(self, page: int = 1, limit: int = 50) -> dict:
        """List products in a Printify shop (paginated).
        Returns JSON with keys like: data (list), current_page, last_page, etc. (shape may vary by API version).
        """
        params = {"page": page, "limit": min(limit, 50)}
        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            return r.json()

    def get_product(self, product_id: str) -> dict:
        """Fetch a single product with full spec (variants, print_areas, etc.)."""
        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products/{product_id}.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(
                    f"{e} — body: {r.text}",
                    request=e.request, response=e.response
                )
            return r.json()

    def duplicate_product(self, template_id: str, title: str | None = None,
                          description: str | None = None, preserve_ids: bool = True) -> dict:
        """
        Duplicate by reading template product and creating a new one with properly slimmed fields.
        """
        tpl = self.get_product(template_id)
        return self.duplicate_from_template(
            template=tpl,
            title=title or (tpl.get("title") or tpl.get("name") or "") + " (Copy)",
            description=description or tpl.get("description") or "",
            tags=tpl.get("tags") or []
        )

    def create_product(self, product_spec: dict):
        """Create a product draft in Printify.
        product_spec should include blueprint_id, print_provider_id, variants, print_areas, title, description, tags
        """
        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products.json"
        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers, json=product_spec)
            r.raise_for_status()
            return r.json()

    import json
    from pathlib import Path
    import httpx

    def update_product(self, product_id: str, product_spec: dict) -> dict:
        """
        PUT an update to a Printify product.
        Logs headers + JSON payload before sending.
        """
        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products/{product_id}.json"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"

        # --- Debug log start ---
        debug_dir = Path("data")
        debug_dir.mkdir(exist_ok=True)
        debug_path = debug_dir / "printify_last_request.json"

        debug_data = {
            "method": "PUT",
            "url": url,
            "headers": headers,
            "json": product_spec,
        }

        with debug_path.open("w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 80)
        print("PRINTIFY UPDATE REQUEST:")
        print(json.dumps(debug_data, indent=2, ensure_ascii=False))
        print("=" * 80 + "\n")
        # --- Debug log end ---

        with httpx.Client(timeout=120) as client:
            r = client.put(url, headers=headers, json=product_spec)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                # print the full response body for debugging
                print("\n--- PRINTIFY RESPONSE BODY ---")
                print(r.text)
                print("------------------------------\n")
                raise httpx.HTTPStatusError(
                    f"{e} — body: {r.text}",
                    request=e.request,
                    response=e.response,
                )
            return r.json()

    def ensure_front_with_image(self, product_json: dict, *, image_id: str, x=0.5, y=0.5, scale=1.0, angle=0) -> dict:
        """
        Returns a minimal product payload that sets a FRONT placeholder with the provided image.
        Uses all enabled variants on the product for the front print_area.
        """
        variants = product_json.get("variants") or []
        enabled_variant_ids = [int(v["id"]) for v in variants if v.get("is_enabled", True)]
        # Build a single front print area
        front_area = {
            "variant_ids": enabled_variant_ids,
            "placeholders": [{
                "position": "front",
                "images": [{
                    "id": image_id,
                    "x": float(x), "y": float(y),
                    "scale": float(scale), "angle": int(angle)
                }]
            }]
        }
        return {"print_areas": [front_area]}

    def publish_to_shopify(self, product_id: str, publish_details: dict | None = None):
        """Publish a Printify product to connected Shopify store.
        publish_details may include: {"title": True, "description": True, "images": True, "variants": True}
        """
        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products/{product_id}/publish.json"
        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers,
                            json=publish_details or {"title": True, "description": True, "images": True,
                                                     "variants": True})
            r.raise_for_status()
            return r.json()

    def upload_image_by_url(self, *, url: str, file_name: str = "art.png") -> dict:
        """Upload an image into the Printify media library by URL; returns the upload JSON incl. 'id'."""
        endpoint = f"{PRINTIFY_API_BASE}/uploads/images.json"
        payload = {"file_name": file_name, "url": url}
        with httpx.Client(timeout=120) as client:
            r = client.post(endpoint, headers=self.headers, json=payload)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(f"{e} — body: {r.text}", request=e.request, response=e.response)
            return r.json()

    def get_blueprint_provider_variants(self, blueprint_id: int | str, print_provider_id: int | str) -> dict:
        """
        Get the provider-specific variants for a blueprint.
        Docs: /v1/catalog/blueprints/{blueprint_id}/print_providers/{print_provider_id}/variants.json
        """
        url = f"{PRINTIFY_API_BASE}/catalog/blueprints/{int(blueprint_id)}/print_providers/{int(print_provider_id)}/variants.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(f"{e} — body: {r.text}", request=e.request, response=e.response)
            return r.json()

    def list_blueprint_providers(self, blueprint_id: int | str) -> dict:
        """Helper: list providers for a blueprint (for clearer errors/fallbacks)."""
        url = f"{PRINTIFY_API_BASE}/catalog/blueprints/{int(blueprint_id)}/print_providers.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise httpx.HTTPStatusError(f"{e} — body: {r.text}", request=e.request, response=e.response)
            return r.json()

    def upload_image_file(self, *, file_path: str, file_name: str | None = None) -> dict:
        """
        Upload a local image to the Printify media library using JSON payload.
        The v1 API expects either {"url": "..."} OR {"file_name": "...", "contents": "<base64>"}.
        Returns JSON including uploaded image 'id'.
        """
        endpoint = f"{PRINTIFY_API_BASE}/uploads/images.json"
        fp = Path(file_path)
        if not fp.exists():
            raise FileNotFoundError(file_path)

        name = file_name or fp.name
        contents_b64 = base64.b64encode(fp.read_bytes()).decode("utf-8")
        payload = {
            "file_name": name,
            "contents": contents_b64
        }

        with httpx.Client(timeout=180) as client:
            # IMPORTANT: JSON, not multipart; must include Content-Type header
            r = client.post(endpoint, headers=self.headers, json=payload)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                # surface server body for quicker debugging
                raise httpx.HTTPStatusError(f"{e} — body: {r.text}", request=e.request, response=e.response)
            return r.json()

