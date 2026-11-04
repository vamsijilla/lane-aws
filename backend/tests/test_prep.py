"""Pages are prepared before they are read: turned upright, straightened, flattened, lit evenly."""

import io
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from lane.config import get_settings
from lane.documents import imaging, ocr, prep
from lane.documents.triage import TriageConfig, triage_image
from tests.docs_factory import png_bytes, text_image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from make_perturbations import _phone_photo  # the photo perturbation itself

CFG = prep.PrepConfig()
LINES = [
    f"Policy period {i}: 12/15/2023 to 12/15/2024, total incurred {1000 + i * 37}"
    for i in range(26)
]


@pytest.fixture(scope="module")
def page() -> Image.Image:
    return text_image(lines=LINES)


def reads(img: Image.Image) -> int:
    return sum(1 for w in ocr.read_words(img) if "incurred" in w.text.lower())


@pytest.mark.parametrize(
    ("turn", "expected"),
    # PIL rotates counterclockwise; prep reports the clockwise turn that undoes it
    [(90, 90), (-90, 270), (180, 180)],
)
def test_a_turned_page_is_turned_upright(page: Image.Image, turn: int, expected: int) -> None:
    p = prep.prepare(page.rotate(turn, expand=True), photo=False, cfg=CFG)
    assert p.turned == expected and p.image.size == page.size
    assert reads(p.image) >= 24


def test_an_upright_page_is_left_alone(page: Image.Image) -> None:
    p = prep.prepare(page, photo=False, cfg=CFG)
    assert not p.changed and p.image.size == page.size


def test_a_tilt_is_straightened() -> None:
    p = prep.prepare(text_image(lines=LINES, angle=3), photo=False, cfg=CFG)
    assert p.steps == ["Straightened a 3 degree tilt"]
    assert abs(imaging.estimate_skew(p.image)) < CFG.deskew_min_deg


def test_a_phone_photo_is_flattened_and_evened(page: Image.Image) -> None:
    photo = _phone_photo(page)
    assert prep.find_page(photo, CFG) is not None
    p = prep.prepare(photo, photo=True, cfg=CFG)
    assert p.flattened and "Evened out a shadow" in p.steps
    w, h = p.image.size
    assert 0.6 < w / h < 0.8  # a page again, not the desk around it
    assert reads(p.image) >= reads(photo)


def test_no_outline_means_no_flattening(page: Image.Image) -> None:
    assert prep.find_page(page, CFG) is None  # a flat scan fills the frame: nothing to cut


def test_ocr_input_is_enlarged_when_small(page: Image.Image) -> None:
    small = page.resize((800, 1131))
    tuned, factor = prep.for_ocr(small, CFG)
    assert factor > 1 and tuned.width == round(800 * factor)


def test_triage_keeps_the_original_and_says_what_it_did(page: Image.Image) -> None:
    cfg = replace(TriageConfig.from_settings(get_settings()), ocr=True)  # tests turn OCR off
    r = triage_image(png_bytes(_phone_photo(page)), cfg)
    assert r.prep and r.prep[0] == "Flattened a photo of the page"
    assert r.original_png and r.render_png and r.original_png != r.render_png
    assert (r.width, r.height) == Image.open(io.BytesIO(r.render_png)).size
    assert r.layout_json  # OCR ran on the prepared page, in its pixels
