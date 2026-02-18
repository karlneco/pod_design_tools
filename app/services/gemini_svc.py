import os
from pathlib import Path
import mimetypes
from io import BytesIO
import httpx
from PIL import Image

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_IMAGE_FALLBACK_MODELS = [
    m.strip() for m in os.getenv("GEMINI_IMAGE_FALLBACK_MODELS", "").split(",") if m.strip()
]


def _load_image_from_path(path_str: str) -> tuple[bytes, str]:
    p = Path(path_str)
    data = p.read_bytes()
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return data, mime


def _load_image_from_url(url: str) -> tuple[bytes, str]:
    with httpx.Client(timeout=60) as client:
        r = client.get(url)
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        mime = ctype if ctype.startswith("image/") else "image/jpeg"
        return r.content, mime


def _bytes_to_pil(image_bytes: bytes) -> Image.Image:
    return Image.open(BytesIO(image_bytes))


def _pil_to_png_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _extract_generated_image_bytes(response) -> bytes | None:
    parts = getattr(response, "parts", None) or []
    for part in parts:
        try:
            if hasattr(part, "as_image"):
                img = part.as_image()
                if img is not None:
                    return _pil_to_png_bytes(img)
        except Exception:
            pass
        inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if not data:
            continue
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            try:
                import base64
                return base64.b64decode(data)
            except Exception:
                continue

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        cparts = getattr(content, "parts", None) or []
        for part in cparts:
            try:
                if hasattr(part, "as_image"):
                    img = part.as_image()
                    if img is not None:
                        return _pil_to_png_bytes(img)
            except Exception:
                pass
            inline = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if not data:
                continue
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                try:
                    import base64
                    return base64.b64decode(data)
                except Exception:
                    continue
    return None


def generate_lifestyle_images(
    prompt: str,
    *,
    num_images: int = 1,
    reference_local_paths: list[str] | None = None,
    reference_urls: list[str] | None = None,
    image_aspect_ratio: str | None = None,
    image_size: str | None = None,
    model_override: str | None = None,
) -> list[dict]:
    """Generate lifestyle images with Gemini image model.

    Returns a list of dicts: [{"bytes": b"...", "mime_type": "image/png"}, ...]
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY missing in environment.")
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")

    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        raise ValueError(
            "google-genai is not installed. Add it to requirements and rebuild the container."
        ) from e

    client = genai.Client(api_key=GEMINI_API_KEY)

    local_paths = [p for p in (reference_local_paths or []) if p]
    urls = [u for u in (reference_urls or []) if u]
    pil_refs: list[Image.Image] = []

    for p in local_paths:
        img, _ = _load_image_from_path(p)
        pil_refs.append(_bytes_to_pil(img))
    for u in urls:
        img, _ = _load_image_from_url(u)
        pil_refs.append(_bytes_to_pil(img))

    out = []
    n = max(1, min(int(num_images or 1), 10))
    for _ in range(n):
        payload = [prompt] + pil_refs
        chosen_model = (model_override or GEMINI_IMAGE_MODEL).strip()
        model_chain = [chosen_model] + [m for m in GEMINI_IMAGE_FALLBACK_MODELS if m != chosen_model]
        image_bytes = None
        last_err = None
        cfg = None
        if image_aspect_ratio or image_size:
            cfg = types.GenerateContentConfig(
                image_config=types.ImageConfig(
                    aspect_ratio=image_aspect_ratio or "1:1",
                    image_size=image_size or "1K",
                )
            )
        for model_name in model_chain:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=payload,
                    config=cfg,
                )
                image_bytes = _extract_generated_image_bytes(response)
                if image_bytes:
                    break
                last_err = ValueError(f"Model {model_name} returned no image data.")
            except Exception as e:
                msg = str(e)
                last_err = e
                if "404" in msg or "not found" in msg.lower() or "not supported" in msg.lower():
                    continue
                raise
        if not image_bytes:
            if last_err:
                raise ValueError(str(last_err))
            raise ValueError("Gemini response did not include an image. Check model access and safety filters.")
        out.append({"bytes": image_bytes, "mime_type": "image/png"})

    return out
