from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import Config
from ..extensions import store

PERSONAS_COLLECTION = "personas"
DEFAULT_AGE_SEGMENTS = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]


def personas_dir() -> Path:
    p = Config.ASSETS_DIR / "personas"
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


def list_personas(active_only: bool = True) -> list[dict]:
    meta_items = store.list(PERSONAS_COLLECTION)
    by_filename = {}
    for item in meta_items:
        fn = str(item.get("image_filename") or "").strip()
        if fn:
            by_filename[fn] = item

    out: list[dict] = []
    base = personas_dir()
    for p in sorted(base.iterdir()):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        item = by_filename.get(p.name, {})
        active = bool(item.get("active", True))
        if active_only and not active:
            continue
        label = str(item.get("label") or p.stem.replace("_", " ")).strip()
        persona_id = str(item.get("id") or _slugify(p.stem))
        out.append({
            "id": persona_id,
            "key": f"persona:{p.name}",
            "label": label,
            "image_filename": p.name,
            "image_url": f"/assets/personas/{p.name}",
            "age_options": parse_age_segments(item.get("age_segments")),
            "notes": str(item.get("notes") or ""),
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
        "source": source or existing.get("source") or "upload",
        "active": bool(active),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    store.upsert(PERSONAS_COLLECTION, persona_id, doc)
    return doc

