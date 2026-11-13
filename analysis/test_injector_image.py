"""The committed injector ROM (data/samples/injector_aapl.mem) is what the model builds
now: a stale file would make the board measure a different image than the model scores."""

from analysis.injector_image import OUT, image_text


def test_committed_rom_matches_model() -> None:
    text, _ = image_text()
    assert OUT.read_text() == text
