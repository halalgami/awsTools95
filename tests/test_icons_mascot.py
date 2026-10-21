import pytest

from aws95.kit import icons
from aws95.kit.mascot import cloudy_pixmap


def test_every_icon_is_16x16_with_known_palette():
    for name in icons.ICON_NAMES:
        rows = icons._ICONS[name]
        assert len(rows) == 16, name
        for row in rows:
            assert len(row) == 16, (name, row)
            assert set(row) <= set(icons._PALETTE), (name, set(row) - set(icons._PALETTE))


@pytest.mark.parametrize("size", [16, 32, 48])
def test_icon_pixmap_sizes(qapp, size):
    pm = icons.icon_pixmap("computer", size)
    assert pm.width() == size and pm.height() == size and not pm.isNull()


def test_icon_pixmap_has_visible_pixels(qapp):
    img = icons.icon_pixmap("money", 32).toImage()
    assert any(img.pixelColor(x, y).alpha() > 0 for x in range(32) for y in range(32))


def test_unknown_icon_and_bad_size(qapp):
    with pytest.raises(KeyError):
        icons.icon_pixmap("nope")
    with pytest.raises(ValueError):
        icons.icon_pixmap("app", 20)


def test_qicon_has_two_sizes(qapp):
    ic = icons.icon("folder")
    assert not ic.isNull()
    assert {s.width() for s in ic.availableSizes()} >= {16, 32}


def test_cloudy_pixmap(qapp):
    pm = cloudy_pixmap(64)
    img = pm.toImage()
    assert pm.width() == 64
    opaque = sum(1 for x in range(64) for y in range(64) if img.pixelColor(x, y).alpha() > 0)
    assert opaque > 64 * 64 * 0.25
