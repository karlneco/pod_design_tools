import base64
from pathlib import Path
import httpx
from urllib.parse import urlencode
from io import BytesIO
from PIL import Image


def _normalize_color_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _escape_graphql_search_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


class ShopifyClient:
    def __init__(self, store_domain: str, admin_token: str, api_version: str = "2024-10"):
        self.domain = store_domain
        self.token = admin_token
        self.api_version = api_version
        self.base = f"https://{self.domain}/admin/api/{self.api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json",
        }

    def product_url(self, handle: str | None) -> str | None:
        if not handle:
            return None
        return f"https://{self.domain.replace('.myshopify.com', '')}.myshopify.com/products/{handle}"

    def upload_product_images(self, product_id: str, image_paths: list[str]):
        """Upload one or more local image files to Shopify.

        Files will be converted to WebP (quality=90) in-memory before encoding to base64
        to keep payloads small (PNG files can be large). If conversion fails we fall
        back to the raw file bytes.
        """
        def _to_webp_bytes(path: str) -> bytes:
            try:
                with Image.open(path) as img:
                    # Preserve alpha when present; ensure mode is RGB or RGBA
                    if img.mode not in ("RGB", "RGBA"):
                        if "A" in img.mode:
                            img = img.convert("RGBA")
                        else:
                            img = img.convert("RGB")
                    buf = BytesIO()
                    # Use quality=90 as requested; method=6 gives a good compression/quality tradeoff
                    img.save(buf, format="WEBP", quality=90, method=6)
                    return buf.getvalue()
            except Exception:
                # Conversion failed; let caller handle fallback
                raise

        uploaded = []
        with httpx.Client(timeout=60) as client:
            for p in image_paths:
                pth = Path(p)
                try:
                    img_bytes = _to_webp_bytes(str(pth))
                except Exception:
                    # If conversion fails for any reason, fall back to reading raw bytes
                    img_bytes = pth.read_bytes()

                b64 = base64.b64encode(img_bytes).decode("utf-8")
                payload = {"image": {"attachment": b64}}
                url = f"{self.base}/products/{product_id}/images.json"
                r = client.post(url, headers=self.headers, json=payload)
                r.raise_for_status()
                uploaded.append(r.json())
        return uploaded

    def list_all_products(self, limit: int = 250) -> list[dict]:
        """Fetch all products via REST pagination using page_info.
        """
        products = []
        params = {"limit": min(limit, 250), "status": "active"}
        next_page_info = None
        with httpx.Client(timeout=60) as client:
            while True:
                query = params.copy()
                if next_page_info:
                    query = {"limit": params["limit"], "page_info": next_page_info}
                url = f"{self.base}/products.json?{urlencode(query)}"
                r = client.get(url, headers=self.headers)
                r.raise_for_status()
                data = r.json()
                items = data.get("products", [])
                products.extend(items)

                # Parse Link header for rel="next"
                link = r.headers.get("Link")
                if link and "rel=\"next\"" in link:
                    # link format: <...page_info=XYZ>; rel="next"
                    try:
                        start = link.find("page_info=") + len("page_info=")
                        end = link.find(">", start)
                        next_page_info = link[start:end]
                    except Exception:
                        next_page_info = None
                else:
                    break
        return products

    def update_product(self, product_id: str, payload: dict) -> dict:
        """Update a Shopify product.
        
        Args:
            product_id: The Shopify product ID
            payload: Dictionary with product fields to update (title, body_html, tags, status, etc.)
            
        Returns:
            The updated product dictionary from Shopify
        """
        url = f"{self.base}/products/{product_id}.json"
        # Shopify API requires the payload to be wrapped in a "product" key
        request_payload = {"product": payload}
        with httpx.Client(timeout=60) as client:
            r = client.put(url, headers=self.headers, json=request_payload)
            r.raise_for_status()
            data = r.json()
            return data.get("product", {})

    def get_product(self, product_id: str) -> dict:
        """Fetch a single product by ID from Shopify (returns product dict or raises)."""
        url = f"{self.base}/products/{product_id}.json"
        with httpx.Client(timeout=60) as client:
            r = client.get(url, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            # Shopify returns { "product": { ... } }
            return data.get("product", {})

    @staticmethod
    def _to_product_gid(product_id: str) -> str:
        pid = str(product_id)
        return pid if pid.startswith("gid://") else f"gid://shopify/Product/{pid}"

    def _graphql(self, query: str, variables: dict) -> dict:
        url = f"{self.base}/graphql.json"
        payload = {"query": query, "variables": variables}
        with httpx.Client(timeout=60) as client:
            r = client.post(url, headers=self.headers, json=payload)
            r.raise_for_status()
            body = r.json()
            if body.get("errors"):
                messages = ", ".join(e.get("message", "Unknown GraphQL error") for e in body["errors"])
                raise ValueError(messages)
            return body.get("data") or {}

    def _extract_existing_category(self, product: dict | None) -> dict | None:
        p = product or {}
        category = p.get("category") or p.get("product_category")
        if isinstance(category, dict):
            cid = category.get("id")
            if cid:
                return {
                    "id": str(cid),
                    "name": category.get("name"),
                    "fullName": category.get("fullName") or category.get("full_name"),
                }
        if isinstance(category, str) and category.strip():
            return {"id": category.strip(), "name": None, "fullName": None}
        return None

    def _infer_target_category_terms(self, product: dict) -> list[str]:
        title = str(product.get("title") or "").lower()
        ptype = str(product.get("product_type") or product.get("type") or "").lower()
        tags = product.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip().lower() for t in tags.split(",") if t.strip()]
        else:
            tags = [str(t).strip().lower() for t in tags if str(t).strip()]
        haystack = " ".join([title, ptype, " ".join(tags)])

        if "hoodie" in haystack or "hooded sweatshirt" in haystack or "sweatshirt" in haystack:
            return ["Hoodies", "Hoodies & Sweatshirts"]
        if "t-shirt" in haystack or "tshirt" in haystack or "tee" in haystack:
            return ["T-Shirts", "T Shirts"]
        return []

    def _search_taxonomy_category(self, term: str) -> dict | None:
        query = """
        query TaxonomyCategorySearch($search: String!) {
          taxonomy {
            categories(first: 20, search: $search) {
              nodes {
                id
                name
                fullName
                isLeaf
              }
            }
          }
        }
        """
        data = self._graphql(query, {"search": term})
        nodes = ((((data.get("taxonomy") or {}).get("categories") or {}).get("nodes")) or [])
        if not nodes:
            return None

        term_norm = _normalize_color_name(term)

        def _rank(node: dict) -> tuple[int, int]:
            name = str(node.get("name") or "").lower()
            full_name = str(node.get("fullName") or "").lower()
            score = 0
            if term_norm and term_norm in name:
                score += 12
            if term_norm and term_norm in full_name:
                score += 10
            if term_norm in ("t-shirts", "t shirts") and "t-shirts" in full_name:
                score += 25
            if term_norm in ("hoodies", "hoodies & sweatshirts") and "hoodies" in full_name:
                score += 25
            if "apparel & accessories" in full_name:
                score += 5
            if "clothing" in full_name:
                score += 3
            if str(node.get("isLeaf")).lower() == "true":
                score += 2
            return (-score, len(full_name), len(name))

        sorted_nodes = sorted(nodes, key=_rank)
        chosen = sorted_nodes[0]
        return {
            "id": chosen.get("id"),
            "name": chosen.get("name"),
            "fullName": chosen.get("fullName"),
        }

    def _set_product_category(self, product_id: str, category_gid: str) -> dict:
        product_gid = self._to_product_gid(product_id)
        mutation = """
        mutation SetProductCategory($input: ProductSetInput!, $synchronous: Boolean!) {
          productSet(input: $input, synchronous: $synchronous) {
            product {
              id
              category {
                id
                name
                fullName
              }
            }
            userErrors {
              field
              message
              code
            }
          }
        }
        """
        data = self._graphql(
            mutation,
            {
                "synchronous": True,
                "input": {
                    "id": product_gid,
                    "category": category_gid,
                },
            },
        )
        payload = (data.get("productSet") or {})
        user_errors = payload.get("userErrors") or []
        if user_errors:
            msg = "; ".join(e.get("message", "Unknown user error") for e in user_errors)
            raise ValueError(msg)
        category = ((payload.get("product") or {}).get("category")) or {}
        return {
            "id": category.get("id"),
            "name": category.get("name"),
            "fullName": category.get("fullName"),
        }

    def ensure_product_category_for_swatches(self, product_id: str, product: dict | None = None) -> dict:
        product = product or self.get_product(product_id)
        existing = self._extract_existing_category(product)
        if existing and existing.get("id"):
            return {"updated": False, "reason": "already_set", "category": existing}

        terms = self._infer_target_category_terms(product)
        if not terms:
            return {"updated": False, "reason": "unable_to_infer", "category": None}

        chosen = None
        for term in terms:
            chosen = self._search_taxonomy_category(term)
            if chosen and chosen.get("id"):
                break

        if not chosen or not chosen.get("id"):
            return {"updated": False, "reason": "not_found", "category": None, "terms": terms}

        updated_category = self._set_product_category(product_id, chosen["id"])
        return {"updated": True, "reason": "set", "category": updated_category}

    def get_color_option_link_status(self, product_id: str) -> dict:
        """Return current Shopify link status for Color/Colour option values."""
        product_gid = self._to_product_gid(product_id)
        query = """
        query ProductColorLinkStatus($id: ID!) {
          product(id: $id) {
            options {
              id
              name
              optionValues {
                id
                name
                linkedMetafieldValue
              }
            }
          }
        }
        """
        data = self._graphql(query, {"id": product_gid})
        options = (((data.get("product") or {}).get("options")) or [])
        color_option = next(
            (opt for opt in options if _normalize_color_name(opt.get("name")) in ("color", "colour")),
            None,
        )
        if not color_option:
            return {
                "total_color_values": 0,
                "linked_after": 0,
                "state": "no_color_option",
                "needs_mapping": False,
            }

        values = color_option.get("optionValues") or []
        total = 0
        linked = 0
        for v in values:
            name = str(v.get("name") or "").strip()
            if not name:
                continue
            total += 1
            if str(v.get("linkedMetafieldValue") or "").strip():
                linked += 1

        if total == 0:
            state = "no_color_option"
            needs_mapping = False
        elif linked >= total:
            state = "mapped"
            needs_mapping = False
        else:
            state = "needs_mapping"
            needs_mapping = True

        return {
            "total_color_values": total,
            "linked_after": linked,
            "state": state,
            "needs_mapping": needs_mapping,
        }

    def apply_color_swatches(self, product_id: str) -> dict:
        """Link Color option values to shopify.color-pattern entries."""
        product = self.get_product(product_id)
        category_update = self.ensure_product_category_for_swatches(product_id, product=product)
        color_names: list[str] = []
        for opt in (product.get("options") or []):
            name = _normalize_color_name(opt.get("name"))
            if name in ("color", "colour"):
                values = opt.get("values") or []
                color_names = [str(v).strip() for v in values if str(v).strip()]
                break

        if not color_names:
            raise ValueError("Product has no Color/Colour option values to apply swatches to.")

        product_gid = self._to_product_gid(product_id)
        query = """
        query ProductColorOption($id: ID!) {
          product(id: $id) {
            id
            options {
              id
              name
              linkedMetafield {
                namespace
                key
              }
              optionValues {
                id
                name
                linkedMetafieldValue
              }
            }
          }
        }
        """
        data = self._graphql(query, {"id": product_gid})
        gql_product = (data or {}).get("product") or {}
        options = gql_product.get("options") or []
        color_option = next(
            (opt for opt in options if _normalize_color_name(opt.get("name")) in ("color", "colour")),
            None,
        )
        if not color_option:
            raise ValueError("Shopify GraphQL did not return a Color/Colour option for this product.")

        values_by_name = {
            _normalize_color_name(v.get("name")): v
            for v in (color_option.get("optionValues") or [])
            if v.get("id") and v.get("name")
        }

        updates: list[dict] = []
        skipped_no_pattern: list[str] = []
        skipped_unmatched: list[str] = []
        skipped_already_linked: list[str] = []

        def _find_color_pattern_metaobject_id(color_name: str) -> str | None:
            search = f"display_name:'{_escape_graphql_search_value(color_name)}'"
            q = """
            query ColorPatternLookup($query: String!) {
              metaobjects(type: "shopify--color-pattern", first: 10, query: $query) {
                nodes {
                  id
                  displayName
                }
              }
            }
            """
            result = self._graphql(q, {"query": search})
            nodes = (((result.get("metaobjects") or {}).get("nodes")) or [])
            normalized = _normalize_color_name(color_name)
            exact = next((n for n in nodes if _normalize_color_name(n.get("displayName")) == normalized), None)
            if exact:
                return exact.get("id")
            return (nodes[0].get("id") if nodes else None)

        for color_name in color_names:
            gql_value = values_by_name.get(_normalize_color_name(color_name))
            if not gql_value:
                skipped_unmatched.append(color_name)
                continue

            linked_value = str(gql_value.get("linkedMetafieldValue") or "").strip()
            if linked_value:
                skipped_already_linked.append(color_name)
                continue

            pattern_id = _find_color_pattern_metaobject_id(color_name)
            if not pattern_id:
                skipped_no_pattern.append(color_name)
                continue

            updates.append(
                {
                    "id": gql_value["id"],
                    "linkedMetafieldValue": pattern_id,
                }
            )

        if not updates:
            return {
                "updated": 0,
                "total_color_values": len(color_names),
                "linked_after": len(skipped_already_linked),
                "skipped_no_pattern": skipped_no_pattern,
                "skipped_unmatched": skipped_unmatched,
                "skipped_already_linked": skipped_already_linked,
                "category_update": category_update,
            }

        mutation = """
        mutation ApplyColorSwatchesViaLinkedMetafield(
          $productId: ID!,
          $option: OptionUpdateInput!,
          $optionValuesToUpdate: [OptionValueUpdateInput!]
        ) {
          productOptionUpdate(
            productId: $productId,
            option: $option,
            optionValuesToUpdate: $optionValuesToUpdate
          ) {
            userErrors {
              field
              message
              code
            }
          }
        }
        """
        result = self._graphql(
            mutation,
            {
                "productId": product_gid,
                "option": {
                    "id": color_option["id"],
                    "linkedMetafield": {"namespace": "shopify", "key": "color-pattern"},
                },
                "optionValuesToUpdate": updates,
            },
        )

        user_errors = ((result.get("productOptionUpdate") or {}).get("userErrors") or [])
        if user_errors:
            msg = "; ".join(e.get("message", "Unknown user error") for e in user_errors)
            raise ValueError(msg)

        return {
            "updated": len(updates),
            "total_color_values": len(color_names),
            "linked_after": len(skipped_already_linked) + len(updates),
            "skipped_no_pattern": skipped_no_pattern,
            "skipped_unmatched": skipped_unmatched,
            "skipped_already_linked": skipped_already_linked,
            "category_update": category_update,
        }
