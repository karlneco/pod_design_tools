1. Create virtualenv and install:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill keys
```

2. Place at least one mockup template under `assets/mockups/` (e.g., `flatlay_white_tee.png`).
   - If your template differs in size, adjust the placement in `index.html` or pass via API POST body when generating mockups.

3. Run the app:

```bash
python app.py
```

4. Add a design via the UI and test:
   - Click **AI: Metadata** to generate title/description/tags using your local documents for context.
   - Click **AI: Colors** to get top garment color suggestions.
   - Click **Generate Mockups** to composite the PNG onto your flat-lay.

5. Printify integration (draft -> publish):
   - Get your `shop_id`, `blueprint_id`, `print_provider_id`, and variant/print area specs from Printify.
   - `POST /api/designs/<slug>/printify/create-product` with JSON payload like:

```json
{
  "shop_id": "123456",
  "blueprint_id": 6,
  "print_provider_id": 14,
  "variants": [ {"id": 4011, "price": 2400, "is_enabled": true} ],
  "print_areas": [ {"variant_ids": [4011], "placeholders": [{"position": "front", "images": [{"id": "external", "url": "https://.../design.png", "x": 0, "y": 0, "scale": 1.0}]}]} ]
}
```

   - Then publish:

```json
{
  "shop_id": "123456",
  "product_id": "<returned product id>",
  "publish_details": {"title": true, "description": true, "images": true, "variants": true}
}
```

6. Shopify image upload:
   - After Printify creates the Shopify product, use its `product_id` to upload more mockups via:

```json
{
  "image_paths": ["generated_mockups/kyoto-bamboo-2025/mockup_flatlay_white_tee.png"]
}
```

## Notes & Roadmap
- JSON-only storage (no file copying) per your requirement; we store only paths.
- Later: add lifestyle mockups (Placeit API or other). Create `services/placeit.py` similar to others.
- Consider adding a `products.json` collection to track Shopify/Printify IDs and handle updates.
- Add webhook endpoints (Shopify/Printify) for sync status; out of scope for initial skeleton.
- Add bulk tools: batch AI metadata, batch mockups, batch publish.
- Add Shopify metafields for personas/collections, and product options for color.
- Add variant color suggestions based on `generated.colors` output.
- Implement image background-safe areas & distort overlay for curved garments if needed.
