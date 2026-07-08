"""Generate the Open Financial Terminal app icon — a black "OFT" wordmark on a white squircle tile.

Minimal monochrome, polished: a flat white iOS/macOS-style squircle (superellipse) tile with a bold
near-black "OFT" wordmark (Segoe UI Black), optically kerned and centered. The wordmark is sized for
presence — it spans ~0.82 of the tile width at hero sizes and grows toward 0.96 at the tiny 16/32 px
window-bar frames so it still reads big in the fixed taskbar / title-bar slot. Every embedded size is
rendered INDEPENDENTLY (8x supersample for the tiny 16/32 px frames, 4x otherwise) so the letters stay
crisp at taskbar size.

Writes:
    packaging/oft.ico            - 6 sizes (16..256), embedded in the exe + WebView2 window + installer
    packaging/oft.png            - 256px master (dev pywebview window icon)
    packaging/oft-512.png        - 512px master (store / README / social)
    packaging/oft-1024.png       - 1024px master
    frontend/public/favicon.ico  - 16/32/48 for the browser tab / served SPA
    frontend/public/apple-touch-icon.png - 180px for iOS / Safari pinned / PWA

Regenerate with:
    "..\\backend\\.venv\\Scripts\\python.exe" make_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── monochrome palette ───────────────────────────────────────────────────────
WHITE = (255, 255, 255, 255)        # tile background
INK = (20, 23, 28, 255)             # near-black #14171c — wordmark

SIZES = [256, 128, 64, 48, 32, 16]
SQUIRCLE_N = 5.0                    # superellipse exponent (~iOS squircle)
INSET = 4                           # master-units of transparent margin around the tile


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    # Segoe UI Black: refined, contemporary, heavy enough to read tiny. Arial Black is the fallback.
    for p in (r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _squircle_points(res: int, k: float, samples: int = 1440) -> list[tuple[float, float]]:
    """Superellipse boundary |x/a|^n + |y/b|^n = 1, as a polygon."""
    c = res / 2.0
    a = res / 2.0 - INSET * k
    pts = []
    for i in range(samples):
        t = 2 * math.pi * i / samples
        ct, st = math.cos(t), math.sin(t)
        x = c + a * math.copysign(abs(ct) ** (2.0 / SQUIRCLE_N), ct)
        y = c + a * math.copysign(abs(st) ** (2.0 / SQUIRCLE_N), st)
        pts.append((x, y))
    return pts


def _tile_mask(res: int, k: float) -> Image.Image:
    msk = Image.new("L", (res, res), 0)
    ImageDraw.Draw(msk).polygon(_squircle_points(res, k), fill=255)
    return msk


def render(out_px: int) -> Image.Image:
    """Render the wordmark tile at exactly out_px. 8x supersample for tiny frames, else 4x."""
    ss = 8 if out_px <= 32 else 4
    res = out_px * ss

    base = Image.new("RGBA", (res, res), WHITE)      # flat white tile (squircle-cut via mask below)
    crisp = Image.new("RGBA", (res, res), (0, 0, 0, 0))
    dc = ImageDraw.Draw(crisp)

    text = "OFT"
    # Bold fill for presence: the wordmark spans ~0.82 of the tile at hero sizes, and even more
    # (up to 0.96) at the tiny window-bar frames so it reads big at the fixed small slot.
    target = res * (0.96 if out_px <= 16 else 0.93 if out_px <= 32 else 0.88 if out_px <= 48 else 0.82)
    gap_frac = 0.05 if out_px < 64 else 0.13

    def measure(f):
        bbs = [dc.textbbox((0, 0), c, font=f, anchor="lt") for c in text]
        inkw = [b[2] - b[0] for b in bbs]
        g = gap_frac * (sum(inkw) / len(inkw))
        return bbs, inkw, g, sum(inkw) + g * (len(text) - 1)

    # fit font size so the optically-spaced ink span ≈ target
    size = int(target)
    for _ in range(10):
        _, _, _, total = measure(_load_font(size))
        if size <= 9 or abs(total - target) <= 1:
            break
        size = max(9, int(round(size * target / total)))

    f = _load_font(size)
    bbs, inkw, g, total = measure(f)
    lbear = [b[0] for b in bbs]
    ink_top = min(b[1] for b in bbs)
    ink_bot = max(b[3] for b in bbs)
    y_top = res / 2 - (ink_top + ink_bot) / 2        # optical vertical centering on the ink box

    cursor = (res - total) / 2                        # even optical gaps between ink edges
    for c, w, lb in zip(text, inkw, lbear):
        dc.text((cursor - lb, y_top), c, font=f, fill=INK, anchor="lt")
        cursor += w + g

    out = Image.alpha_composite(base, crisp)
    out.putalpha(_tile_mask(res, res / 256.0))
    return out.resize((out_px, out_px), Image.LANCZOS)


def _save_ico(path: Path, frames: list[Image.Image], sizes: list[int]) -> None:
    frames[0].save(path, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[1:])
    print(f"wrote {path}")


def main() -> None:
    here = Path(__file__).resolve().parent
    imgs = {s: render(s) for s in SIZES}

    _save_ico(here / "oft.ico", [imgs[s] for s in SIZES], SIZES)

    for name, px in (("oft.png", 256), ("oft-512.png", 512), ("oft-1024.png", 1024)):
        (imgs[px] if px in imgs else render(px)).save(here / name)
        print(f"wrote {here / name}")

    public = here.parent / "frontend" / "public"
    public.mkdir(parents=True, exist_ok=True)
    _save_ico(public / "favicon.ico", [imgs[s] for s in (48, 32, 16)], [48, 32, 16])
    render(180).save(public / "apple-touch-icon.png")    # iOS / Safari pinned / PWA
    print(f"wrote {public / 'apple-touch-icon.png'}")


if __name__ == "__main__":
    main()
