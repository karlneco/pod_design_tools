import os
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

        def _slim_placeholders(placeholders: list[dict]) -> list[dict]:
            """
            Keep only placeholders that actually have at least one usable image.
            An image is “usable” if it already has a valid media id OR we can upload by URL.
            """
            out = []
            for ph in (placeholders or []):
                imgs = []
                for img in (ph.get("images") or []):
                    iid = _image_id_from_template(img)
                    if iid:
                        imgs.append({
                            "id": iid,
                            "x": _to_float(img.get("x", 0)),
                            "y": _to_float(img.get("y", 0)),
                            "scale": _to_float(img.get("scale", 1)),
                            "angle": _to_int(img.get("angle", 0)),  # MUST be integer per validator
                        })
                # Only keep this placeholder if it has at least one image
                if imgs:
                    entry = {"position": ph.get("position", "front"), "images": imgs}
                    if isinstance(ph.get("decoration_method"), str):
                        entry["decoration_method"] = ph["decoration_method"]
                    out.append(entry)
            return out

        def _slim_print_areas(print_areas: list[dict]) -> list[dict]:
            slim = []
            for pa in (print_areas or []):
                placeholders = _slim_placeholders(pa.get("placeholders", []))
                if not placeholders:
                    continue  # skip empty print area entirely
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
            "title": title,
            "description": description,
            "tags": tags or template.get("tags", []),
            "blueprint_id": template["blueprint_id"],
            "print_provider_id": template["print_provider_id"],
            "variants": _slim_variants(template["variants"]),
            "print_areas": _slim_print_areas(template.get("print_areas", [])),
            # DO NOT include: images (mockups), options (read-only), id/created_at/updated_at, sales_channel_properties
        }

        url = f"{PRINTIFY_API_BASE}/shops/{self.shop_id}/products.json"
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
