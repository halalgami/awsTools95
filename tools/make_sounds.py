"""Synthesise AWS 95's original system sounds and launch jingle (deterministic, numpy only).

Run: uv run python tools/make_sounds.py   -> writes aws95/assets/sounds/*.wav
All audio is generated from first principles here; nothing is sampled from any existing product.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT_DIR = Path(__file__).resolve().parents[1] / "aws95" / "assets" / "sounds"
EVENTS = ("startup", "shutdown", "window_open", "window_close", "minimize", "maximize", "menu_popup",
          "click", "balloon", "error", "question", "refresh_done", "saver_start")

NOTE = {"G3": 196.00, "A3": 220.00, "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23,
        "G4": 392.00, "A4": 440.00, "B4": 493.88, "C5": 523.25, "D5": 587.33, "E5": 659.25,
        "F5": 698.46, "G5": 783.99, "A5": 880.00, "B5": 987.77, "C6": 1046.50, "E6": 1318.51}


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur), dtype=np.float64) / SR


def _env(n: int, attack: float = 0.005, decay: float = 4.0) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0.0, 1.0)
    return a * np.exp(-decay * t)


def bell(freq: float, dur: float, index: float = 2.5, ratio: float = 1.4, decay: float = 3.5) -> np.ndarray:
    t = _t(dur)
    env = _env(len(t), 0.004, decay)
    mod = index * env * np.sin(2 * np.pi * freq * ratio * t)
    return env * np.sin(2 * np.pi * freq * t + mod)


def square(freq: float, dur: float, decay: float = 6.0, duty: float = 0.5) -> np.ndarray:
    t = _t(dur)
    wave_ = np.where((t * freq) % 1.0 < duty, 1.0, -1.0) * 0.35
    return wave_ * _env(len(t), 0.002, decay)


def sweep(f0: float, f1: float, dur: float) -> np.ndarray:
    t = _t(dur)
    freq = np.linspace(f0, f1, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * _env(len(t), 0.003, 9.0) * 0.6


def pad(freqs: list[float], dur: float) -> np.ndarray:
    t = _t(dur)
    out = sum(np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) for f in freqs)
    fade = np.minimum(1.0, t / 0.4) * np.minimum(1.0, (dur - t) / 0.8)
    return out / len(freqs) * fade * 0.35


def place(total: float, parts: list[tuple[float, np.ndarray]]) -> np.ndarray:
    out = np.zeros(int(SR * total))
    for start, sig in parts:
        i = int(SR * start)
        j = min(len(out), i + len(sig))
        out[i:j] += sig[: j - i]
    return out


def _jingle() -> np.ndarray:
    parts = []
    for k, n in enumerate(["C4", "G4", "B4", "D5", "E5"]):           # Cmaj9 rising
        parts.append((0.10 * k, bell(NOTE[n], 1.6) * 0.5))
    for k, n in enumerate(["A3", "F4", "C5", "E5"]):                 # Fmaj7/A
        parts.append((0.95 + 0.09 * k, bell(NOTE[n], 1.4) * 0.45))
    for k, n in enumerate(["G3", "D4", "B4", "E5"]):                 # G6
        parts.append((1.65 + 0.09 * k, bell(NOTE[n], 1.2) * 0.45))
    for n in ["C4", "G4", "E5", "B5"]:                               # final Cmaj7 shimmer
        parts.append((2.25, bell(NOTE[n], 1.15, index=1.8, decay=2.2) * 0.4))
    parts.append((0.0, pad([NOTE["C4"], NOTE["E4"], NOTE["G4"]], 3.4)))
    return place(3.4, parts)


def _shutdown() -> np.ndarray:
    parts = [(0.0, bell(NOTE["E5"], 1.2) * 0.5), (0.18, bell(NOTE["C5"], 1.2) * 0.5),
             (0.36, bell(NOTE["G4"], 1.2) * 0.5), (0.54, bell(NOTE["C4"], 1.0, decay=2.5) * 0.55),
             (0.0, pad([NOTE["C4"], NOTE["G4"]], 1.6))]
    return place(1.6, parts)


RECIPES = {
    "startup": _jingle,
    "shutdown": _shutdown,
    "window_open": lambda: sweep(600, 1400, 0.12),
    "window_close": lambda: sweep(1400, 600, 0.12),
    "minimize": lambda: sweep(1200, 300, 0.18),
    "maximize": lambda: sweep(300, 1200, 0.18),
    "menu_popup": lambda: square(1800, 0.04, decay=60),
    "click": lambda: square(2400, 0.02, decay=120),
    "balloon": lambda: bell(NOTE["E6"], 0.35, index=1.2, decay=7.0),
    "error": lambda: place(0.4, [(0.0, square(NOTE["A4"], 0.4, 5)), (0.0, square(NOTE["C5"], 0.4, 5)),
                                 (0.0, square(NOTE["E5"] * 0.94, 0.4, 5))]),
    "question": lambda: place(0.35, [(0.0, bell(NOTE["G5"], 0.2, decay=10)), (0.12, bell(NOTE["C6"], 0.23, decay=9))]),
    "refresh_done": lambda: place(0.45, [(0.0, bell(NOTE["C5"], 0.25, decay=9)), (0.1, bell(NOTE["E5"], 0.25, decay=9)),
                                         (0.2, bell(NOTE["G5"], 0.25, decay=8))]),
    "saver_start": lambda: place(0.5, [(0.0, bell(NOTE["A5"], 0.5, index=1.0, decay=5)),
                                       (0.08, bell(NOTE["E5"], 0.42, index=1.0, decay=5))]),
}


def render(name: str) -> np.ndarray:
    sig = np.asarray(RECIPES[name](), dtype=np.float64)
    k = min(int(0.008 * SR), len(sig) // 4)                 # 8 ms linear fade-out: every file ends at 0
    sig[-k:] *= np.linspace(1.0, 0.0, k)
    peak = float(np.max(np.abs(sig))) or 1.0
    return (sig / peak * 0.89).astype(np.float32)          # normalise to about -1 dBFS


def _write(path: Path, samples: np.ndarray) -> None:
    pcm = np.round(samples * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def write_all(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in EVENTS:
        p = out_dir / f"{name}.wav"
        _write(p, render(name))
        paths.append(p)
    return paths


def main() -> None:
    for p in write_all():
        print(f"{p.name:20s} {p.stat().st_size:>8,d} bytes")


if __name__ == "__main__":
    main()
