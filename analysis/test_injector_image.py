"""The committed injector ROMs (data/samples/injector_aapl.mem, DATA_W 8, and
injector_aapl_w64.mem, DATA_W 64) are what the model builds now: a stale file would make
the board measure a different image than the model scores."""

from analysis.injector_image import OUT, image_text


def test_committed_roms_match_model() -> None:
    text, wide, _ = image_text()
    assert OUT.read_text() == text
    assert OUT.with_stem(OUT.stem + "_w64").read_text() == wide
