import struct

from PySide6.QtGui import QImage

from aws95.app import ICON_DIR, app_icon
from tools import make_icons


def test_committed_icon_files_exist(qapp):
    for size in make_icons.SIZES:
        img = QImage(str(ICON_DIR / f"aws95-{size}.png"))
        assert not img.isNull() and img.width() == size and img.height() == size, size
    assert (ICON_DIR / "aws95.ico").stat().st_size > 0


def test_ico_has_every_size(qapp, tmp_path):
    images = {s: make_icons.render(s) for s in make_icons.SIZES}
    make_icons.write_ico(tmp_path / "x.ico", images)
    data = (tmp_path / "x.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind, count) == (0, 1, len(make_icons.ICO_SIZES))
    dims = [struct.unpack_from("<B", data, 6 + 16 * i)[0] or 256 for i in range(count)]
    assert dims == list(make_icons.ICO_SIZES)


def test_render_is_opaque_in_the_middle(qapp):
    img = make_icons.render(256)
    assert img.pixelColor(128, 128).alpha() == 255          # the window body, not transparent
    assert img.pixelColor(2, 2).alpha() == 0                # macOS-style margin at large sizes


def test_app_icon_has_all_sizes(qapp):
    icon = app_icon()
    assert not icon.isNull()
    assert {s.width() for s in icon.availableSizes()} >= {16, 32, 256}
