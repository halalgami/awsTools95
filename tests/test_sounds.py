import hashlib
import wave
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import Settings
from aws95.shell.sound import DEFAULT_OFF, EVENTS, SOUND_DIR, SoundBank
from tools import make_sounds


def test_render_is_deterministic_and_bounded():
    for name in make_sounds.EVENTS:
        a, b = make_sounds.render(name), make_sounds.render(name)
        assert np.array_equal(a, b), name
        assert a.dtype == np.float32 and np.max(np.abs(a)) <= 1.0
    assert len(make_sounds.render("startup")) / make_sounds.SR <= 3.5
    for name in make_sounds.EVENTS:
        if name not in ("startup", "shutdown"):
            assert len(make_sounds.render(name)) / make_sounds.SR <= 0.6, name


def test_write_all_format_and_budget(tmp_path):
    paths = make_sounds.write_all(tmp_path)
    assert {p.stem for p in paths} == set(make_sounds.EVENTS)
    total = 0
    for p in paths:
        with wave.open(str(p)) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)
        total += p.stat().st_size
    assert total < 1_000_000


def test_committed_assets_match_generator(tmp_path):
    fresh = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in make_sounds.write_all(tmp_path)}
    committed = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SOUND_DIR.glob("*.wav")}
    assert committed == fresh


def test_shell_events_match_generator():
    assert set(EVENTS) == set(make_sounds.EVENTS)


class FakeEffect:
    def __init__(self, path, parent):
        self.path, self.plays, self.volume = path, 0, None

    def setVolume(self, v):  # noqa: N802
        self.volume = v

    def play(self):
        self.plays += 1


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_play_respects_mute_and_toggles(settings):
    effects = {}
    bank = SoundBank(settings, effect_factory=lambda p, parent: effects.setdefault(p.stem, FakeEffect(p, parent)))
    assert bank.play("balloon") is True and effects["balloon"].plays == 1
    assert effects["balloon"].volume == pytest.approx(0.8)
    assert bank.play("click") is False                     # default off
    settings.set_sound_event_enabled("balloon", False)
    assert bank.play("balloon") is False
    settings.sound_muted = True
    assert bank.play("error") is False
    assert bank.preview("balloon") is True                 # explicit preview ignores mute/toggles


def test_unknown_event_raises(settings):
    with pytest.raises(KeyError):
        SoundBank(settings, effect_factory=lambda p, parent: FakeEffect(p, parent)).play("nope")


def test_failing_backend_is_silent(settings):
    def boom(path, parent):
        raise RuntimeError("no audio device")
    bank = SoundBank(settings, effect_factory=boom)
    assert bank.loaded() == () and bank.play("startup") is False


def test_missing_sound_dir_is_silent(settings, tmp_path):
    bank = SoundBank(settings, effect_factory=lambda p, parent: FakeEffect(p, parent), sound_dir=tmp_path / "nope")
    assert bank.play("startup") is False


def test_default_off_set():
    assert DEFAULT_OFF == frozenset({"click"})


def test_every_event_ends_at_zero():
    for name in make_sounds.EVENTS:
        a = make_sounds.render(name)
        assert a[-1] == 0.0, name
        assert np.max(np.abs(a[-10:])) < 0.01, name


def test_play_failure_in_effect_is_silent(settings):
    class Boom(FakeEffect):
        def play(self):
            raise RuntimeError("device lost")
    bank = SoundBank(settings, effect_factory=lambda p, parent: Boom(p, parent))
    assert bank.play("balloon") is False


def test_play_uses_configured_volume(settings):
    effects = {}
    bank = SoundBank(settings, effect_factory=lambda p, parent: effects.setdefault(p.stem, FakeEffect(p, parent)))
    settings.sound_volume = 35
    assert bank.play("balloon") is True
    assert effects["balloon"].volume == pytest.approx(0.35)
