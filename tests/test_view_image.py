import dataclasses

import pytest

from nova.tools import documents
from nova.tools.registry import Registry
from nova.tools.screen import MAX_JPEG_BYTES, fit_for_vision


class FakeLLM:
    def __init__(self):
        self.calls = []

    def look(self, jpeg: bytes, question: str) -> str:
        self.calls.append((jpeg, question))
        return "a red rectangle"


@pytest.fixture
def vision(cfg):
    llm = FakeLLM()
    registry = Registry()
    documents.register(registry, cfg, llm)
    return llm, (lambda **args: registry.invoke("view_image", args))


def test_png_goes_to_the_vision_model(vision, make_image):
    llm, view_image = vision
    path = make_image("shot.png").resolve()
    out = view_image(path=str(path), question="What colour is it?")
    assert out == f"[image 64x32, {path}]\na red rectangle"
    jpeg, question = llm.calls[0]
    assert jpeg[:2] == b"\xff\xd8"
    assert question == "What colour is it?"


def test_default_question(vision, make_image):
    llm, view_image = vision
    view_image(path=str(make_image("a.png")))
    assert llm.calls[0][1] == documents.DEFAULT_QUESTION


@pytest.mark.parametrize(
    "name", ["photo.heic", "PHOTO.HEIC", "anim.gif", "pic.webp", "scan.tiff", "old.bmp", "SNAP.JPG"]
)
def test_formats(vision, make_image, name):
    _, view_image = vision
    assert view_image(path=str(make_image(name))).startswith("[image 64x32,")


def test_phone_photos_are_turned_upright(vision, make_image):
    _, view_image = vision
    out = view_image(path=str(make_image("portrait.jpg", exif_orientation=6)))
    assert out.startswith("[image 32x64,")


def test_relative_path_uses_the_workspace(vision, make_image):
    _, view_image = vision
    make_image("rel.png")
    assert view_image(path="rel.png").startswith("[image 64x32,")


def test_not_a_picture(vision, tmp_path):
    _, view_image = vision
    (tmp_path / "report.pdf").write_bytes(b"%PDF")
    assert view_image(path=str(tmp_path / "report.pdf")) == (
        "ERROR: report.pdf is not a picture view_image can open. Use read_file for documents."
    )


def test_missing_file(vision, tmp_path):
    _, view_image = vision
    missing = (tmp_path / "nope.png").resolve()
    assert view_image(path=str(missing)) == f"ERROR: {missing} does not exist."


def test_corrupt_picture(vision, tmp_path):
    _, view_image = vision
    (tmp_path / "bad.png").write_bytes(b"not an image")
    assert view_image(path=str(tmp_path / "bad.png")).startswith("ERROR: could not open bad.png:")


def test_workspace_restriction_applies(cfg):
    registry = Registry()
    documents.register(registry, dataclasses.replace(cfg, restrict_to_workspace=True), FakeLLM())
    out = registry.invoke("view_image", {"path": "/Library/Desktop Pictures/x.png"})
    assert "outside the allowed workspace" in out


def test_fit_for_vision_shrinks_big_noisy_images():
    from PIL import Image

    noisy = Image.effect_noise((4000, 3000), 100).convert("RGB")
    jpeg = fit_for_vision(noisy)
    assert jpeg[:2] == b"\xff\xd8"
    assert len(jpeg) <= MAX_JPEG_BYTES


def test_transparent_images_are_shown_on_white(tmp_path):
    from PIL import Image, ImageDraw

    path = tmp_path / "logo.png"
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))  # fully transparent
    ImageDraw.Draw(image).rectangle((10, 5, 20, 15), fill=(0, 0, 0, 255))  # a black mark
    image.save(path)
    opened = documents.open_image(path)
    assert opened.getpixel((0, 0)) == (255, 255, 255)  # background, not black
    assert opened.getpixel((15, 10)) == (0, 0, 0)  # the mark survives


def test_palette_transparency_is_shown_on_white(tmp_path):
    from PIL import Image

    path = tmp_path / "icon.png"
    image = Image.new("P", (10, 10), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)  # 0 = black, 1 = red
    image.paste(1, (5, 5, 10, 10))  # a red square
    image.save(path, transparency=0)  # black is the transparent colour
    opened = documents.open_image(path)
    assert opened.getpixel((0, 0)) == (255, 255, 255)
    assert opened.getpixel((7, 7)) == (255, 0, 0)
