import sys
from pathlib import Path

from PIL import Image

THEME = Path(__file__).resolve().parent.parent / "plymouth" / "homeboard"
DRM = Path("/sys/class/drm")
MAX_WIDTH = 1920


def screen_size(drm=DRM):
    for status in sorted(drm.glob("card*-*/status")):
        if status.read_text().strip() != "connected":
            continue
        modes = (status.parent / "modes").read_text().split()
        if modes:
            width, height = modes[0].split("x")
            return int(width), int(height.rstrip("ip"))
    return 1920, 1080


def render(width, height):
    if width > MAX_WIDTH:
        width, height = MAX_WIDTH, round(height * MAX_WIDTH / width)
    scale_x = width / 1920
    scale_y = height / 1080

    def scaled(name):
        image = Image.open(THEME / name).convert("RGBA")
        size = (int(image.width * scale_x + 0.5), int(image.height * scale_y + 0.5))
        return image.resize(size, Image.LANCZOS)

    background = Image.open(THEME / "background.png").convert("RGBA")
    frame = background.resize((width, height), Image.LANCZOS)
    for name, x, y in [("track.png", 518, 747), ("label-boot.png", 516, 696)]:
        image = scaled(name)
        frame.alpha_composite(image, (int(x * scale_x), int(y * scale_y)))
    return frame.convert("RGB")


def main(argv):
    if len(argv) == 4:
        width, height = int(argv[2]), int(argv[3])
    else:
        width, height = screen_size()
    render(width, height).save(argv[1])


if __name__ == "__main__":
    main(sys.argv)
