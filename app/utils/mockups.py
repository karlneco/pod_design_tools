from pathlib import Path
from PIL import Image

# Simple compositing: paste the design PNG onto mockup template at a named placement

def generate_mockups_for_design(design_png_path: str, templates: list[str], placements: dict, out_dir: Path, scale: float = 1.0):
    out_dir.mkdir(parents=True, exist_ok=True)
    design = Image.open(design_png_path).convert("RGBA")

    out_paths = []
    for t in templates:
        template = Image.open(t).convert("RGBA")
        # Pick a placement key; default to 'center'
        place = placements.get("center", {"x": template.width//2, "y": template.height//2, "max_w": int(template.width*0.5), "max_h": int(template.height*0.5)})

        # Scale design to fit within max_w x max_h while preserving aspect ratio
        max_w, max_h = int(place.get("max_w", 2000)*scale), int(place.get("max_h", 2000)*scale)
        d_w, d_h = design.size
        ratio = min(max_w / d_w, max_h / d_h)
        new_size = (int(d_w * ratio), int(d_h * ratio))
        d_resized = design.resize(new_size, Image.LANCZOS)

        # Compute top-left
        top_left = (int(place["x"] - new_size[0] / 2), int(place["y"] - new_size[1] / 2))

        composite = template.copy()
        composite.alpha_composite(d_resized, dest=top_left)

        out_path = out_dir / f"mockup_{Path(t).stem}.png"
        composite.convert("RGB").save(out_path, "PNG", optimize=True)
        out_paths.append(out_path)

    return out_paths
