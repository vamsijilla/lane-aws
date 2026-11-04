from pathlib import Path

import pytest
from PIL import Image

from lane.documents import imaging, pdf
from tests.docs_factory import text_image

PACKET = Path(__file__).resolve().parents[2] / "data" / "packet_cobalt_mesa"


def test_sharpness_drops_with_blur() -> None:
    sharp = imaging.sharpness(imaging.gray(text_image()))
    blurred = imaging.sharpness(imaging.gray(text_image(blur=3)))
    assert sharp > 120
    assert blurred < 40


def test_colorfulness_separates_photos_from_scans() -> None:
    assert imaging.colorfulness(text_image()) < 5
    assert imaging.colorfulness(text_image(color=True)) > 15


@pytest.mark.parametrize("angle", [-3.0, 0.0, 2.5])
def test_estimate_skew_recovers_the_tilt(angle: float) -> None:
    # PIL rotates counter-clockwise; the estimator returns the correction angle.
    assert imaging.estimate_skew(text_image(angle=angle)) == pytest.approx(-angle, abs=0.5)


def test_sideways_pages_are_detected_on_a_real_scan() -> None:
    page = next(pdf.iter_pages((PACKET / "Q1 2026.pdf").read_bytes(), dpi=100))
    upright = imaging.open_image(page.png)
    assert not imaging.looks_sideways(upright)
    assert imaging.looks_sideways(upright.transpose(Image.Transpose.ROTATE_90))
    assert imaging.looks_sideways(upright.transpose(Image.Transpose.ROTATE_270))
