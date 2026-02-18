from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import Config
from ..extensions import store

PERSONAS_COLLECTION = "personas"
DEFAULT_AGE_SEGMENTS = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
ALLOWED_GENDERS = {"unspecified", "male", "female", "non-binary"}


def personas_dir() -> Path:
    p = Config.DATA_DIR / "personas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slugify(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or ""))
    out = "-".join(part for part in out.split("-") if part)
    return out or "persona"


def parse_age_segments(value) -> list[str]:
    if isinstance(value, str):
        raw = [x.strip() for x in value.split(",")]
    elif isinstance(value, list):
        raw = [str(x).strip() for x in value]
    else:
        raw = []
    out: list[str] = []
    for seg in raw:
        if seg in DEFAULT_AGE_SEGMENTS and seg not in out:
            out.append(seg)
    return out or DEFAULT_AGE_SEGMENTS.copy()


def normalize_gender(value) -> str:
    g = str(value or "").strip().lower()
    return g if g in ALLOWED_GENDERS else "unspecified"


def normalize_generation_orientation(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in {"square", "portrait"} else "square"


def normalize_generation_resolution(value) -> int:
    try:
        n = int(value)
    except Exception:
        n = 2048
    if n in {3840, 4096}:
        return 4096
    return n if n in {1024, 2048} else 2048


def _normalize_render_history(value) -> list[dict]:
    items = value if isinstance(value, list) else []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip()
        if not filename:
            continue
        out.append({
            "filename": filename,
            "image_url": f"/assets/personas/{filename}",
            "prompt": str(item.get("prompt") or "").strip(),
            "reference_filename": str(item.get("reference_filename") or "").strip(),
            "reference_image_url": (
                f"/assets/personas/{str(item.get('reference_filename') or '').strip()}"
                if str(item.get("reference_filename") or "").strip()
                else ""
            ),
            "source": str(item.get("source") or "upload").strip() or "upload",
            "created_at": item.get("created_at"),
        })
    return out


def list_personas(active_only: bool = True) -> list[dict]:
    meta_items = store.list(PERSONAS_COLLECTION)

    out: list[dict] = []
    for item in sorted(meta_items, key=lambda x: str(x.get("label") or x.get("id") or "").lower()):
        active = bool(item.get("active", True))
        if active_only and not active:
            continue
        image_filename = str(item.get("image_filename") or "").strip()
        if not image_filename:
            continue
        label = str(item.get("label") or str(item.get("id") or "")).strip()
        persona_id = str(item.get("id") or _slugify(label))
        render_history = _normalize_render_history(item.get("render_history"))
        if not render_history:
            render_history = [{
                "filename": image_filename,
                "image_url": f"/assets/personas/{image_filename}",
                "prompt": str(item.get("generation_prompt") or ""),
                "reference_filename": "",
                "reference_image_url": "",
                "source": str(item.get("source") or "upload"),
                "created_at": item.get("created_at"),
            }]
        out.append({
            "id": persona_id,
            "key": f"persona:{image_filename}",
            "label": label,
            "image_filename": image_filename,
            "image_url": f"/assets/personas/{image_filename}",
            "age_options": parse_age_segments(item.get("age_segments")),
            "notes": str(item.get("notes") or ""),
            "gender": normalize_gender(item.get("gender")),
            "archetype": str(item.get("archetype") or ""),
            "location": str(item.get("location") or ""),
            "occupation": str(item.get("occupation") or ""),
            "generation_prompt": str(item.get("generation_prompt") or ""),
            "generation_orientation": normalize_generation_orientation(item.get("generation_orientation")),
            "generation_resolution": normalize_generation_resolution(item.get("generation_resolution")),
            "render_history": render_history,
            "source": str(item.get("source") or "upload"),
            "active": active,
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        })
    return out


def upsert_persona(
    *,
    persona_id: str,
    label: str,
    image_filename: str,
    age_segments,
    notes: str = "",
    gender: str = "unspecified",
    archetype: str = "",
    location: str = "",
    occupation: str = "",
    generation_prompt: str | None = None,
    generation_orientation: str | None = None,
    generation_resolution: int | None = None,
    render_history: list[dict] | None = None,
    source: str = "upload",
    active: bool = True,
) -> dict:
    existing = store.get(PERSONAS_COLLECTION, persona_id) or {}
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": persona_id,
        "label": str(label or "").strip() or persona_id,
        "image_filename": image_filename,
        "age_segments": parse_age_segments(age_segments),
        "notes": str(notes or "").strip(),
        "gender": normalize_gender(gender if gender is not None else existing.get("gender")),
        "archetype": str(archetype if archetype is not None else existing.get("archetype") or "").strip(),
        "location": str(location if location is not None else existing.get("location") or "").strip(),
        "occupation": str(occupation if occupation is not None else existing.get("occupation") or "").strip(),
        "generation_prompt": str(
            generation_prompt if generation_prompt is not None else existing.get("generation_prompt") or ""
        ).strip(),
        "generation_orientation": normalize_generation_orientation(
            generation_orientation if generation_orientation is not None else existing.get("generation_orientation")
        ),
        "generation_resolution": normalize_generation_resolution(
            generation_resolution if generation_resolution is not None else existing.get("generation_resolution")
        ),
        "render_history": _normalize_render_history(
            render_history if render_history is not None else existing.get("render_history")
        ),
        "source": source or existing.get("source") or "upload",
        "active": bool(active),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    store.upsert(PERSONAS_COLLECTION, persona_id, doc)
    return doc
