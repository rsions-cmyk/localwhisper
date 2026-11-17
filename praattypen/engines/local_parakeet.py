from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from praattypen.engines.base import TranscriptionEngine


class LocalParakeetEngine(TranscriptionEngine):
    name = "local_parakeet"

    def __init__(self, model: str = "mlx-community/parakeet-tdt-0.6b-v3") -> None:
        self.model = model

    def build_command(self, audio_path: Path, output_dir: Path) -> list[str]:
        local_cli = Path(sys.executable).parent / "parakeet-mlx"
        executable = str(local_cli) if local_cli.exists() else "parakeet-mlx"
        return [
            executable,
            str(audio_path),
            "--model",
            self.model,
            "--output-dir",
            str(output_dir),
            "--output-format",
            "txt",
        ]

    def transcribe(self, audio_path: Path) -> str:
        with tempfile.TemporaryDirectory() as output_dir:
            command = self.build_command(audio_path, Path(output_dir))
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except FileNotFoundError as error:
                raise RuntimeError(
                    "parakeet-mlx is not installed. Install local STT dependencies with "
                    "`.venv/bin/python -m pip install parakeet-mlx`."
                ) from error
            except subprocess.CalledProcessError as error:
                raise RuntimeError(error.stderr or error.stdout or "parakeet-mlx failed") from error

            txt_files = sorted(Path(output_dir).glob("*.txt"))
            if not txt_files:
                return ""
            return txt_files[0].read_text(encoding="utf-8").strip()
