import pytest

from aws95.shell.screensaver import SAVERS, saver_titles


def _non_black(img, step=4):
    return sum(1 for x in range(0, img.width(), step) for y in range(0, img.height(), step)
               if img.pixelColor(x, y).name() != "#000000")


@pytest.mark.parametrize("name", sorted(SAVERS))
def test_saver_animates(qtbot, name):
    saver = SAVERS[name](seed=7)
    qtbot.addWidget(saver)
    saver.resize(320, 200)
    saver.reset()
    for _ in range(90):
        saver.step()
    assert _non_black(saver.canvas) > 0, name


@pytest.mark.parametrize("name", sorted(SAVERS))
def test_deterministic_with_seed(qtbot, name):
    a, b = SAVERS[name](seed=3), SAVERS[name](seed=3)
    for s in (a, b):
        qtbot.addWidget(s)
        s.resize(200, 120)
        s.reset()
        for _ in range(30):
            s.step()
    assert a.canvas == b.canvas


def test_timer_only_while_visible(qtbot):
    s = SAVERS["starfield"](seed=1)
    qtbot.addWidget(s)
    assert not s.is_running()
    s.show()
    qtbot.waitExposed(s)
    assert s.is_running()
    s.hide()
    assert not s.is_running()


def test_titles():
    assert saver_titles() == {"starfield": "Starfield", "mystify": "Mystify", "pipes": "Pipes",
                              "maze": "Maze Walk", "bounce": "Bouncing Logo"}


@pytest.mark.parametrize("name", sorted(SAVERS))
def test_step_before_show_does_not_raise(qtbot, name):
    s = SAVERS[name](seed=2)
    qtbot.addWidget(s)
    s.step()


def test_bounce_tiny_canvas_does_not_strobe(qtbot):
    s = SAVERS["bounce"](seed=4)
    qtbot.addWidget(s)
    s.resize(120, 90)
    s.reset()
    hues = {s.hue}
    for _ in range(60):
        s.step()
        hues.add(s.hue)
    assert len(hues) <= 3
    assert 0 <= s.x <= 120 and 0 <= s.y <= 90


def test_pipes_start_with_several_pipes(qtbot):
    s = SAVERS["pipes"](seed=2)
    qtbot.addWidget(s)
    s.resize(320, 200)
    s.reset()
    assert len(s.pipes) >= 3


def test_starfield_clears_to_black_each_frame(qtbot):
    s = SAVERS["starfield"](seed=2)
    qtbot.addWidget(s)
    s.resize(320, 200)
    s.reset()
    for _ in range(20):
        s.step()
    assert _non_black(s.canvas, 1) < s.canvas.width() * s.canvas.height() // 10
