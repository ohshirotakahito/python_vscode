"""Build header-matching ICOs from the actual Segoe Fluent Icons font file."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from shared import ui_theme
from shared.app_icons import SYMBOLS, icon_path


def main():
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'SegoeIcons.ttf'
    # Load the file explicitly, avoiding toolkit font substitution and missing-glyph boxes.
    font = ImageFont.truetype(str(font_path), 160)
    for app_id, (glyph, accent) in SYMBOLS.items():
        image = Image.new('RGBA', (256, 256))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, 255, 255), radius=60,
                               fill=ui_theme.mix(ui_theme.HEADER_FROM, accent, 0.22))
        left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
        draw.text(((256 - (right - left)) / 2 - left, (256 - (bottom - top)) / 2 - top),
                  glyph, font=font, fill=accent)
        path = icon_path(app_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
        image.save(path.with_suffix('.png'))
        print(app_id)


if __name__ == '__main__':
    main()
