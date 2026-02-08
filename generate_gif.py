#!/usr/bin/env python3
"""Generate an animated GIF demonstrating the circle fill/unfill animation."""

from PIL import Image, ImageDraw, ImageChops

def _generate_fill_frame(color, fill_level, size=128):
    """Generate a circle partially filled from the bottom."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = int(size * 0.15)
    bbox = [margin, margin, size - margin, size - margin]
    stroke_width = max(2, size // 16)

    if fill_level >= 1.0:
        draw.ellipse(bbox, fill=color)
        return image

    draw.ellipse(bbox, outline=color, width=stroke_width)

    if fill_level <= 0:
        return image

    filled = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(filled).ellipse(bbox, fill=color)

    circle_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle_mask).ellipse(bbox, fill=255)

    circle_h = size - 2 * margin
    cutoff_y = margin + int(circle_h * (1.0 - fill_level))
    rect_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(rect_mask).rectangle([0, cutoff_y, size, size], fill=255)

    final_mask = ImageChops.multiply(circle_mask, rect_mask)
    image = Image.composite(filled, image, final_mask)
    return image


def main():
    color = (180, 180, 180, 255)
    num_steps = 20  # More steps for smoother GIF
    size = 128

    frames = []
    # Fill up
    for i in range(num_steps + 1):
        frame = _generate_fill_frame(color, i / num_steps, size=size)
        # Convert RGBA to RGB with dark background for GIF compatibility
        bg = Image.new("RGBA", (size, size), (24, 24, 24, 255))
        composite = Image.alpha_composite(bg, frame)
        frames.append(composite.convert("RGB"))

    # Bounce back down (skip endpoints to avoid duplicate frames)
    for i in range(num_steps - 1, 0, -1):
        frame = _generate_fill_frame(color, i / num_steps, size=size)
        bg = Image.new("RGBA", (size, size), (24, 24, 24, 255))
        composite = Image.alpha_composite(bg, frame)
        frames.append(composite.convert("RGB"))

    output_path = "docs/working-animation.gif"
    import os
    os.makedirs("docs", exist_ok=True)

    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=80,  # ms per frame
        loop=0,       # infinite loop
    )
    print(f"Saved {len(frames)}-frame GIF to {output_path}")


if __name__ == "__main__":
    main()
