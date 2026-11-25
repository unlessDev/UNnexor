"""Locuciones de prueba generadas con las voces TTS de Windows.

Se generan en tiempo de ejecución (no se guarda audio en el repositorio) y
no contienen datos personales.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SAMPLES: dict[str, list[str]] = {
    "es": [
        "Buenos días a todos. Empezamos la reunión con el informe del trimestre.",
        "¿Podrías contarme cuál fue tu experiencia más difícil trabajando en "
        "equipo y cómo la resolviste?",
        "El presupuesto aumentó un quince por ciento respecto del año pasado, "
        "sobre todo en infraestructura y licencias.",
    ],
    "en": [
        "Good morning everyone. Could you explain how the new deployment "
        "pipeline works and why we moved away from manual releases?",
    ],
}

VOICES = {"es": "es", "en": "en"}


def synthesize(text: str, culture_prefix: str, target: Path, pause_ms: int = 0) -> None:
    """WAV con la primera voz instalada cuyo idioma empiece por el prefijo."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name "
        f"-like '{culture_prefix}*' }} | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
        f"$s.SetOutputToWaveFile('{target}');"
        "$p = New-Object System.Speech.Synthesis.PromptBuilder;"
        "foreach ($line in ($env:EXTDATOR_TTS_TEXT -split '\\|')) {"
        f" $p.AppendText($line); $p.AppendBreak([TimeSpan]::FromMilliseconds({pause_ms})) }};"
        "$s.Speak($p); $s.Dispose()"
    )
    env = dict(os.environ, EXTDATOR_TTS_TEXT=text)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        env=env,
        capture_output=True,
        timeout=90,
    )


def synthesize_lines(lines: list[str], language: str, target: Path) -> None:
    """Varias frases separadas por 900 ms de silencio (intervenciones)."""
    synthesize("|".join(lines), VOICES[language], target, pause_ms=900)
