import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PIL import Image
from services.social_bot.generators.image_card import render_card, WIDTH, HEIGHT


def test_render_card_dimensions():
    img = render_card(29067, "Some clue text", "7")
    assert img.size == (WIDTH, HEIGHT)


def test_render_card_is_rgb():
    img = render_card(29067, "Some clue text", "7")
    assert img.mode == "RGB"


def test_render_card_no_letter_count():
    # Should not raise even without a letter count
    img = render_card(29067, "Some clue text", "")
    assert isinstance(img, Image.Image)


def test_render_card_long_clue():
    long_clue = "Eccentric character in broadcasting perhaps going back and changing direction regularly"
    img = render_card(28461, long_clue, "9,4")
    assert img.size == (WIDTH, HEIGHT)


def test_render_card_short_clue():
    img = render_card(29000, "Bird (4)", "4")
    assert img.size == (WIDTH, HEIGHT)


def test_render_card_not_blank():
    img = render_card(29067, "Some clue text", "7")
    # Card should not be a solid colour — check pixel variance
    pixels = list(img.getflattened_data()) if hasattr(img, "getflattened_data") else list(img.getdata())
    unique = len(set(pixels))
    assert unique > 10, "Card appears blank or nearly blank"
