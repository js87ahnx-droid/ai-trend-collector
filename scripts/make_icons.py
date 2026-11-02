"""홈 화면 아이콘 PNG 생성 (시안 v5-icon D안 + 오른쪽 아래 소문자 "ai") — 한 번 만들어 web/에 커밋, CI에선 쓰지 않음.
python scripts/make_icons.py [굵은 글꼴 경로]   (Pillow 필요)"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WEB = Path(__file__).resolve().parents[1] / "web"
FONT = sys.argv[1] if len(sys.argv) > 1 else "C:/Windows/Fonts/arialbd.ttf"
TOP, BOTTOM, ORANGE, AI = (0x1C, 0x25, 0x40), (0x0D, 0x12, 0x22), (0xFF, 0x8A, 0x4C), (0xC9, 0xD5, 0xFF)


def bezier(p0, p1, p2, p3, n=24):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(n))]


def sparkle(cx, cy, r, k):
    """꼭짓점 4개가 안쪽으로 오목하게 이어진 반짝임 — 시안 SVG 경로를 r 기준 비율로 옮김."""
    a, b = r * 4 / 58, r * 18 / 58
    seg = bezier((0, -r), (a, -b), (b, -a), (r, 0))   # 위 꼭짓점 → 오른쪽 꼭짓점 한 구간
    pts = []
    for i in range(4):                                 # 90도씩 돌려 네 구간
        for x, y in seg:
            for _ in range(i):
                x, y = -y, x
            pts.append((x, y))
    return [((cx + x) * k, (cy + y) * k) for x, y in pts]


def icon(n: int) -> Image.Image:
    s = n * 4                                  # 크게 그린 뒤 줄여 가장자리를 매끈하게
    k = s / 180
    img = Image.new("RGB", (s, s))
    d = ImageDraw.Draw(img)
    for y in range(s):                         # 위 → 아래 남색 그라데이션
        f = y / (s - 1)
        d.line([(0, y), (s, y)], fill=tuple(round(a + (b - a) * f) for a, b in zip(TOP, BOTTOM)))
    d.polygon(sparkle(80, 84, 54, k), fill="white")
    d.polygon(sparkle(136, 42, 17, k), fill=ORANGE)
    font = ImageFont.truetype(FONT, round(34 * k))
    d.text((154 * k, 156 * k), "ai", font=font, fill=AI, anchor="rs")
    return img.resize((n, n), Image.LANCZOS)


if __name__ == "__main__":
    for n in (180, 192, 512):
        icon(n).save(WEB / f"icon-{n}.png", optimize=True)
        print(f"icon-{n}.png")
