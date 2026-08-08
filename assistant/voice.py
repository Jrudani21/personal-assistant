"""Local voice in/out — faster-whisper for STT, Windows SAPI (pyttsx3) for TTS.
Both fully offline after the one-time Whisper model download, $0 cost."""
import io
import tempfile
from pathlib import Path

import pyttsx3
from faster_whisper import WhisperModel

_whisper = None


def _get_whisper() -> WhisperModel:
    global _whisper
    if _whisper is None:
        _whisper = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper


def transcribe(audio_bytes: bytes) -> str:
    model = _get_whisper()
    segments, _ = model.transcribe(io.BytesIO(audio_bytes))
    return " ".join(s.text for s in segments).strip()


def speak(text: str) -> bytes:
    """Synthesizes speech and returns WAV bytes."""
    engine = pyttsx3.init()
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "reply.wav"
        engine.save_to_file(text, str(out_path))
        engine.runAndWait()
        return out_path.read_bytes()
