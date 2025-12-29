# core/voice_piper.py
from pathlib import Path
from typing import Optional
import subprocess
import tempfile

import sounddevice as sd
import soundfile as sf


class PiperVoice:
    """
    Обёртка над piper-tts (новый CLI).

    Модели ожидаются в:
    ~/.local/share/piper/voices/piper-ru/piper-voices/ru/ru_RU/<voice>/
    Например: "irina/medium", "ruslan/medium", "dmitri/medium".
    """

    def __init__(self, voice: str = "irina/medium"):
        self.voice = voice

        # Явно используем piper из pipx: ~/.local/bin/piper
        home = Path.home()
        piper_bin = home / ".local" / "bin" / "piper"
        if not piper_bin.is_file():
            raise RuntimeError(
                f"Бинарник piper из pipx не найден: {piper_bin}\n"
                "Проверь, что ставил через `pipx install piper-tts`."
            )
        self.piper_bin = piper_bin

        self.model_path = self._find_model()
        print(f"[Piper] Используем бинарник: {self.piper_bin}")
        print(f"[Piper] Используем модель:   {self.model_path}")

    # ---------- поиск модели ----------

    def _find_model(self) -> Path:
        base = (
            Path.home()
            / ".local"
            / "share"
            / "piper"
            / "voices"
            / "piper-ru"
            / "piper-voices"
            / "ru"
            / "ru_RU"
            / self.voice
        )

        if not base.is_dir():
            raise RuntimeError(f"Каталог с моделью не найден: {base}")

        onnx_files = list(base.glob("*.onnx"))
        if not onnx_files:
            raise RuntimeError(f"ONNX-файл модели не найден в {base}")

        # берём первый .onnx
        return onnx_files[0]

    # ---------- синтез ----------

    def say(self, text: str):
        if not text:
            return

        # временный wav
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        cmd = [
            str(self.piper_bin),
            "--model",
            str(self.model_path),
            "--output_file",
            str(wav_path),
        ]

        print("[Piper] Генерируем речь…")
        subprocess.run(cmd, input=text.encode("utf-8"), check=True)

        print("[Piper] Воспроизводим…")
        data, samplerate = sf.read(wav_path, dtype="float32")
        sd.play(data, samplerate)
        sd.wait()

        try:
            wav_path.unlink()
        except FileNotFoundError:
            pass
