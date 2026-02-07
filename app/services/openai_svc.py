import os
import json
from pathlib import Path
import httpx

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE = os.getenv("OPENAI_BASE", "https://api.openai.com/v1")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

HEADERS = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
}

def _read_file_safely(path_str: str) -> str:
    try:
        p = Path(path_str)
        if p.exists():
            return p.read_text(encoding="utf-8")[:120000]  # bound tokens
    except Exception:
        pass
    return ""

SYSTEM_PROMPT = (
    "You are the Kozakura Designs product copy and merchandising assistant. "
    "Follow our DTG design rules (no gradients, limited colors, bold, legible), "
    "and write concise, high-converting Shopify titles, descriptions, and keywords. "
    "Respect our brand voice: friendly, travel-forward, Japan-prefecture focus."
)

META_USER_PROMPT = (
    "Using the hints and our documents below, propose: \n"
    "1) Product Title (<= 60 chars),\n"
    "2) 3-sentence Description with a short first hook sentence,\n"
    "3) 10-16 SEO keywords (comma-separated),\n"
    "4) 3-5 Shopify tags (comma-separated).\n\n"
    "HINTS: title_hint={title_hint}, collections={collections}, notes={notes}.\n\n"
    "DOCS: personas.md, principles.txt, policies.md below. Be specific (prefecture names, seasons) when relevant."
)

COLORS_USER_PROMPT = (
    "Based on the product concept and typical buyer personas, suggest the top 6 garment colors "
    "(primary tees/hoodies) that will make the design pop for DTG printing (avoid colors that hurt readability). "
    "Return an array of objects with fields: name, hex (approx), why."
)

DESCRIPTION_USER_PROMPT = (
    "Write a short Shopify product description in HTML that follows this exact format:\n"
    "<h2>Title - Streetwear Tee Front Print</h2>\n"
    "<p class=\"p4\">One paragraph, 2–3 sentences. Include one emphasized phrase wrapped as "
    "<span class=\"s2\"><b>...</b></span>. Keep it travel-forward and evocative. "
    "Do NOT describe the graphic or the illustration itself. Focus on mood, place, and vibe.\n\n"
    "End the paragraph with exactly 3 emojis that fit the mood.\n"
    "Use plain ASCII punctuation (avoid smart quotes or en-dashes).\n\n"
    "Product context:\n"
    "Title hint: {title_hint}\n"
    "Tags: {tags}\n"
    "Notes: {notes}\n"
)

def _chat(messages):
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{OPENAI_BASE}/chat/completions", headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


def suggest_metadata(title_hint: str, collections, notes: str, docs_paths: dict):
    personas = _read_file_safely(docs_paths.get("personas_pdf", ""))
    principles = _read_file_safely(docs_paths.get("principles", ""))
    policies = _read_file_safely(docs_paths.get("policies", ""))

    prompt = META_USER_PROMPT.format(title_hint=title_hint, collections=collections, notes=notes)
    content = _chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt + "\n\nPERSONAS:\n" + personas + "\n\nPRINCIPLES:\n" + principles + "\n\nPOLICIES:\n" + policies},
    ])

    # attempt to parse structured blocks
    out = {"title": None, "description": None, "keywords": [], "tags": []}
    try:
        # naive parse by markers
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        title = next((l for l in lines if l.lower().startswith("product title")), None)
        if title:
            out["title"] = title.split(":", 1)[-1].strip()
        # description lines after 'Description'
        desc_idx = next((i for i,l in enumerate(lines) if l.lower().startswith("description")), None)
        if desc_idx is not None:
            out["description"] = " ".join(lines[desc_idx+1:desc_idx+4])
        # keywords
        kw = next((l for l in lines if "keywords" in l.lower()), None)
        if kw:
            kws = kw.split(":",1)[-1]
            out["keywords"] = [k.strip() for k in kws.split(",") if k.strip()]
        # tags
        tg = next((l for l in lines if "shopify tags" in l.lower() or l.lower().startswith("tags")), None)
        if tg:
            tgs = tg.split(":",1)[-1]
            out["tags"] = [t.strip() for t in tgs.split(",") if t.strip()]
    except Exception:
        pass

    # Fallback to raw content
    if not any(out.values()):
        out["description"] = content
    return out


def suggest_colors(design_title: str, collections, notes: str):
    prompt = (
        f"Design: {design_title}\nCollections: {collections}\nNotes: {notes}\n" + COLORS_USER_PROMPT
    )
    try:
        content = _chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        # Try to parse as JSON list
        start = content.find("[")
        end = content.rfind("]") + 1
        if start != -1 and end != -1:
            return json.loads(content[start:end])
    except Exception:
        pass
    # Fallback on API error or parse failure
    return [
        {"name": "Black", "hex": "#000000", "why": "High contrast for bright graphics and white text."},
        {"name": "White", "hex": "#FFFFFF", "why": "Versatile, clean base for colorful designs."},
    ]


def suggest_description(title_hint: str, tags, notes: str):
    prompt = DESCRIPTION_USER_PROMPT.format(
        title_hint=title_hint,
        tags=tags,
        notes=notes or "",
    )
    content = _chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    content = content.strip()
    if content.startswith("```"):
        lines = [l for l in content.splitlines() if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()
    content = content.replace("–", "-").replace("—", "-").replace("“", "\"").replace("”", "\"").replace("’", "'")
    return content
