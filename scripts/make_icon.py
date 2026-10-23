from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icon-512.png"
SIZE = 512
BG = (17, 17, 17)
FRAME = (74, 158, 255)
SKY = (40, 44, 52)
HILL = (46, 160, 90)
SUN = (255, 200, 60)


def make_icon(path: Path = OUT) -> Path:
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((72, 112, 440, 400), radius=28, fill=FRAME)
    d.rectangle((100, 140, 412, 372), fill=SKY)
    d.ellipse((310, 168, 370, 228), fill=SUN)
    d.polygon([(100, 372), (200, 240), (290, 372)], fill=HILL)
    d.polygon([(220, 372), (330, 270), (412, 340), (412, 372)], fill=HILL)
    img.save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    print(make_icon())
