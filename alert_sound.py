"""Generate and play local Sidro notification sounds.

Synthesizes alert WAV files and handles playback for application events;
generated sounds are runtime data rather than published source assets.
"""

from __future__ import annotations

import math
import random
import hashlib
import struct
import subprocess
import threading
import wave
import winsound
from pathlib import Path

import config

SIDRO_VOICE_STYLE = "Sidro Voice"
SIDRO_VOICE_VERSION = 3


def play_alert(event: str = "info"):
    if not getattr(config, "ALERT_SOUND_ENABLED", True):
        return

    def _play():
        try:
            path = generated_alert_path(event)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            try:
                winsound.MessageBeep()
            except Exception:
                pass

    threading.Thread(target=_play, daemon=True).start()


def play_voice_line(phrase: str, cache_key: str = "line"):
    if not getattr(config, "ALERT_SOUND_ENABLED", True):
        return
    phrase = str(phrase or "").strip()
    if not phrase:
        return

    def _play():
        try:
            path = generated_voice_line_path(phrase, cache_key)
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            try:
                winsound.MessageBeep()
            except Exception:
                pass

    threading.Thread(target=_play, daemon=True).start()


def generated_voice_line_path(phrase: str, cache_key: str = "line") -> Path:
    volume = max(0.0, min(1.0, float(getattr(config, "ALERT_SOUND_VOLUME", 0.45))))
    digest = hashlib.sha1(f"{SIDRO_VOICE_VERSION}|{cache_key}|{phrase}".encode("utf-8")).hexdigest()[:12]
    safe_name = f"sidro_voice_{digest}_{int(volume * 100)}.wav"
    out_dir = Path(getattr(config, "GENERATED_SOUNDS_DIR", "generated_sounds"))
    out_dir.mkdir(exist_ok=True)
    path = out_dir / safe_name
    if not path.exists():
        _write_sidro_phrase_wav(path, phrase, "info", volume)
    return path


def generated_alert_path(event: str) -> Path:
    style = getattr(config, "ALERT_SOUND_STYLE", "Random")
    if style == "Custom File":
        custom = Path(str(getattr(config, "ALERT_SOUND_CUSTOM_PATH", ""))).expanduser()
        if custom.exists() and custom.suffix.lower() == ".wav":
            return custom
    if style == "Random":
        choices = [item for item in generated_styles() if item != SIDRO_VOICE_STYLE]
        style = random.choice(choices or generated_styles())
    volume = max(0.0, min(1.0, float(getattr(config, "ALERT_SOUND_VOLUME", 0.45))))
    safe_name = f"{style.lower().replace(' ', '_')}_{event}_{int(volume * 100)}.wav"
    out_dir = Path(getattr(config, "GENERATED_SOUNDS_DIR", "generated_sounds"))
    out_dir.mkdir(exist_ok=True)
    path = out_dir / safe_name
    if not path.exists():
        _write_alert_wav(path, style, event, volume)
    return path


def generated_styles() -> list[str]:
    return [
        SIDRO_VOICE_STYLE,
        "Void Chime",
        "Trade Ping",
        "Soft Pulse",
        "Credit Tick",
        "Orokin Bell",
        "Relic Spark",
        "Cephalon Ping",
        "Market Bell",
        "Quiet Confirm",
    ]


def _write_alert_wav(path: Path, style: str, event: str, volume: float):
    if style == SIDRO_VOICE_STYLE:
        _write_sidro_voice_wav(path, event, volume)
        return

    patterns = {
        "Void Chime": [(392, 0.09), (587, 0.10), (880, 0.16)],
        "Trade Ping": [(660, 0.06), (990, 0.07), (1320, 0.08)],
        "Soft Pulse": [(330, 0.12), (440, 0.14)],
        "Credit Tick": [(1046, 0.04), (1318, 0.04), (1568, 0.08)],
        "Orokin Bell": [(262, 0.08), (523, 0.11), (784, 0.18), (1046, 0.12)],
        "Relic Spark": [(1480, 0.035), (1760, 0.04), (2217, 0.08), (1175, 0.08)],
        "Cephalon Ping": [(740, 0.055), (554, 0.055), (932, 0.11)],
        "Market Bell": [(880, 0.05), (1175, 0.05), (880, 0.12)],
        "Quiet Confirm": [(494, 0.08), (659, 0.12)],
    }
    event_shift = {
        "success": 1.12,
        "trade": 1.0,
        "warning": 0.72,
        "error": 0.55,
        "info": 0.92,
    }.get(event, 1.0)

    sample_rate = 44100
    samples: list[int] = []
    for freq, seconds in patterns.get(style, patterns["Void Chime"]):
        freq *= event_shift
        tone_samples = int(sample_rate * seconds)
        for i in range(tone_samples):
            t = i / sample_rate
            attack = min(1.0, i / max(1, int(sample_rate * 0.012)))
            release = min(1.0, (tone_samples - i) / max(1, int(sample_rate * 0.035)))
            env = min(attack, release)
            wave_a = math.sin(2 * math.pi * freq * t)
            wave_b = 0.28 * math.sin(2 * math.pi * freq * 1.5 * t)
            value = int(32767 * volume * env * 0.45 * (wave_a + wave_b))
            samples.append(max(-32767, min(32767, value)))
        samples.extend([0] * int(sample_rate * 0.035))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def _write_sidro_voice_wav(path: Path, event: str, volume: float):
    phrase = {
        "success": "Done.",
        "trade": "Trade confirmed.",
        "warning": "Attention.",
        "error": "Action failed.",
        "info": "Ready.",
    }.get(event, "Ready.")
    _write_sidro_phrase_wav(path, phrase, event, volume)


def _write_sidro_phrase_wav(path: Path, phrase: str, event: str, volume: float):
    raw_path = path.with_name(f"{path.stem}_raw.wav")
    if _write_windows_tts(raw_path, phrase, volume):
        try:
            _robotize_voice_wav(raw_path, path, volume)
            raw_path.unlink(missing_ok=True)
            return
        except Exception:
            try:
                raw_path.replace(path)
                return
            except Exception:
                pass

    _write_synthetic_voice_fallback(path, event, volume)


def _write_windows_tts(path: Path, phrase: str, volume: float) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path = str(path).replace("'", "''")
    safe_phrase = str(phrase).replace("'", "''")
    safe_volume = int(max(0.0, min(1.0, volume)) * 100)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$female = $synth.GetInstalledVoices() | "
        "Where-Object { $_.Enabled -and $_.VoiceInfo.Gender -eq 'Female' } | "
        "Select-Object -First 1; "
        "if ($female) { $synth.SelectVoice($female.VoiceInfo.Name) }; "
        "$synth.Rate = 0; "
        f"$synth.Volume = {safe_volume}; "
        f"$synth.SetOutputToWaveFile('{safe_path}'); "
        f"$synth.Speak('{safe_phrase}'); "
        "$synth.Dispose();"
    )
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
            creationflags=flags,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and path.exists() and path.stat().st_size > 1000


def _robotize_voice_wav(source: Path, target: Path, volume: float):
    with wave.open(str(source), "rb") as src:
        channels = src.getnchannels()
        width = src.getsampwidth()
        rate = src.getframerate()
        frames = src.readframes(src.getnframes())

    if width != 2:
        source.replace(target)
        return

    samples = list(struct.unpack("<" + "h" * (len(frames) // 2), frames))
    processed: list[int] = []
    drive = 0.98
    chorus_samples = max(1, int(rate * 0.008) * channels)
    chorus_gain = 0.018
    for index, sample in enumerate(samples):
        t = (index // channels) / rate
        shimmer = 0.997 + 0.003 * math.sin(2 * math.pi * 5.2 * t)
        value = int(sample * shimmer * drive)
        if index >= chorus_samples:
            value += int(samples[index - chorus_samples] * chorus_gain)
        value = int(value * max(0.5, min(1.0, volume + 0.45)))
        processed.append(max(-32767, min(32767, value)))

    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in processed))


def _write_synthetic_voice_fallback(path: Path, event: str, volume: float):
    event_base = {
        "success": 680,
        "trade": 620,
        "warning": 480,
        "error": 360,
        "info": 540,
    }.get(event, 540)
    sample_rate = 44100
    samples: list[int] = []
    syllables = [(event_base, 0.16), (event_base * 1.22, 0.13), (event_base * 0.88, 0.18)]
    for freq, seconds in syllables:
        count = int(sample_rate * seconds)
        for i in range(count):
            t = i / sample_rate
            attack = min(1.0, i / max(1, int(sample_rate * 0.018)))
            release = min(1.0, (count - i) / max(1, int(sample_rate * 0.045)))
            env = min(attack, release)
            carrier = math.sin(2 * math.pi * freq * t)
            formant = 0.45 * math.sin(2 * math.pi * freq * 2.07 * t)
            robot = math.sin(2 * math.pi * 38.0 * t) * 0.22 + 0.78
            value = int(32767 * volume * 0.38 * env * robot * (carrier + formant))
            samples.append(max(-32767, min(32767, value)))
        samples.extend([0] * int(sample_rate * 0.035))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
