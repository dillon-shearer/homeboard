import sys
from pathlib import Path

from PIL import Image

COVER = Path(__file__).resolve().parent / "cover"
DRM = Path("/sys/class/drm")
MAX_WIDTH = 1920
STEPS = 14
STEP_MS = 100


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
        image = Image.open(COVER / name).convert("RGBA")
        size = (int(image.width * scale_x + 0.5), int(image.height * scale_y + 0.5))
        return image.resize(size, Image.LANCZOS)

    background = Image.open(COVER / "background.png").convert("RGBA")
    base = background.resize((width, height), Image.LANCZOS)
    track = scaled("track.png")
    track_at = (int(518 * scale_x), int(747 * scale_y))
    base.alpha_composite(track, track_at)
    base.alpha_composite(scaled("label-boot.png"), (int(516 * scale_x), int(696 * scale_y)))

    blocks = scaled("blocks.png")
    blocks_width = 526.3125 * scale_x
    blocks_left = 521 * scale_x
    clip_left = 519.40625 * scale_x
    clip_right = 1400.59375 * scale_x
    blocks_y = int(750 * scale_y)
    frames = []
    for k in range(STEPS):
        x = blocks_left + (k * 2.7 / STEPS - 1) * blocks_width
        left = max(x, clip_left)
        right = min(x + blocks_width, clip_right)
        frame = base.copy()
        crop_width = int(right) - int(left)
        if crop_width > 0:
            start = int(left - x)
            piece = blocks.crop((start, 0, start + crop_width, blocks.height))
            frame.alpha_composite(piece, (int(left), blocks_y))
        frames.append(frame.convert("RGB"))
    bar = (*track_at, track_at[0] + track.width, track_at[1] + track.height)
    return base.convert("RGB"), frames, bar


def animate(width, height):
    still, frames, bar = render(width, height)
    palette = frames[STEPS // 2].quantize(256)
    ground = still.quantize(palette=palette)
    out = []
    for frame in frames:
        image = ground.copy()
        piece = frame.crop(bar).quantize(palette=palette, dither=Image.Dither.NONE)
        image.paste(piece, bar[:2])
        out.append(image)
    return out


def main(argv):
    if len(argv) == 4:
        width, height = int(argv[2]), int(argv[3])
    else:
        width, height = screen_size()
    frames = animate(width, height)
    frames[0].save(
        argv[1], format="GIF", save_all=True, append_images=frames[1:], loop=0, duration=STEP_MS
    )


if __name__ == "__main__":
    main(sys.argv)
