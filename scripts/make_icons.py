"""홈 화면 아이콘 PNG 생성 (시안 v4-icon A안) — 한 번 만들어 web/에 커밋, CI에선 쓰지 않음.
python scripts/make_icons.py [굵은 글꼴 경로]   (Pillow 필요)"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WEB = Path(__file__).resolve().parents[1] / "web"
FONT = sys.argv[1] if len(sys.argv) > 1 else "C:/Windows/Fonts/arialbd.ttf"
TOP, BOTTOM, BAR = (0x4B, 0x74, 0xF5), (0x1F, 0x3F, 0xC4), (0xFF, 0x8A, 0x4C)


def icon(n: int) -> Image.Image:
    s = n * 4                                  # 크게 그린 뒤 줄여 가장자리를 매끈하게
    k = s / 180
    img = Image.new("RGB", (s, s))
    px = img.load()
    for y in range(s):                         # 왼쪽 위 → 오른쪽 아래 대각선 그라데이션
        for x in range(s):
            f = (x + y) / (2 * s - 2)
            px[x, y] = tuple(round(a + (b - a) * f) for a, b in zip(TOP, BOTTOM))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, round(80 * k))
    d.text((90 * k, 112 * k), "AI", font=font, fill="white", anchor="ms")
    d.rounded_rectangle((54 * k, 128 * k, 126 * k, 136 * k), radius=4 * k, fill=BAR)
    return img.resize((n, n), Image.LANCZOS)


for n in (180, 192, 512):
    icon(n).save(WEB / f"icon-{n}.png", optimize=True)
    print(f"icon-{n}.png")
