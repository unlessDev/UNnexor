"""Análisis y resolución de desafíos en pantalla (caso de uso, sin Qt).

* ``fingerprint``/``ChangeDetector``: hash perceptual (dHash 64 bits) de la
  imagen en escala de grises para que el análisis periódico no procese
  capturas sin cambios relevantes.
* ``ChallengeSolver``: elige visión (modelo multimodal) si el router tiene
  un candidato con visión permitido; si no, OCR + modelo de texto; si no
  hay ninguna vía, lo explica. Una resolución a la vez, cancelable.

La captura de pantalla la hace la capa Qt existente (``desktop/screenshot``)
solo por acción del usuario o con el análisis periódico activado
expresamente.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from ..routing.executor import RoutingCancelled, RoutingError
from ..routing.router import Task
from .engine import GenerationEngine
from .prompts import challenge_messages

log = logging.getLogger("extdator.copilot.challenges")


def fingerprint(gray: npt.NDArray[Any]) -> int:
    """dHash: 9×8 bloques promediados; bit = brillo mayor que el vecino."""
    image = np.asarray(gray, dtype=np.float32)
    if image.ndim != 2 or min(image.shape) < 9:
        raise ValueError("Se espera una imagen en escala de grises de al menos 9×9")
    rows = np.array_split(image, 8, axis=0)
    cells = np.array(
        [[block.mean() for block in np.array_split(r, 9, axis=1)] for r in rows]
    )
    bits = (cells[:, 1:] > cells[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class ChangeDetector:
    """¿La pantalla cambió lo suficiente como para volver a analizarla?"""

    def __init__(self, threshold: int = 6) -> None:
        self.threshold = threshold
        self._last: int | None = None

    def should_process(self, print_: int) -> bool:
        if self._last is None or hamming(self._last, print_) > self.threshold:
            self._last = print_
            return True
        return False

    def reset(self) -> None:
        self._last = None


@dataclass(frozen=True)
class ChallengeResult:
    text: str
    method: str  # vision | ocr
    model: str


class ChallengeSolver:
    def __init__(
        self,
        engine: GenerationEngine,
        *,
        ocr: Callable[[list[str]], str] | None = None,
        language: Callable[[], str] = lambda: "es",
    ) -> None:
        self._engine = engine
        self._ocr = ocr  # recibe imágenes base64 y devuelve texto
        self._language = language
        self._cancel = threading.Event()
        self._busy = threading.Lock()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def plan(self) -> tuple[str, str]:
        """(método, explicación) que se usaría ahora."""
        vision = self._engine.decide(Task.VISION)
        if vision.ok and vision.primary is not None:
            return "vision", f"Visión con {vision.primary.label}."
        if self._ocr is not None:
            text = self._engine.decide(Task.CHALLENGE)
            if text.ok and text.primary is not None:
                return (
                    "ocr",
                    f"OCR local + {text.primary.label} (ningún modelo con visión permitido).",
                )
        return "none", (
            "No hay un modelo con visión permitido ni OCR disponible. Instala un "
            "modelo con visión (p. ej. qwen2.5vl:7b) o un motor OCR."
        )

    def solve(
        self,
        images_b64: list[str],
        instruction: str = "",
        *,
        on_chunk: Callable[[str], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
    ) -> ChallengeResult:
        if not images_b64:
            raise ValueError("No hay capturas para analizar.")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Ya hay un análisis en curso.")
        try:
            self._cancel.clear()
            method, explanation = self.plan()
            if method == "none":
                raise RoutingError(explanation)
            if on_notice is not None:
                on_notice(explanation)
            if method == "vision":
                messages = challenge_messages(
                    instruction, language=self._language(), images=images_b64
                )
                task = Task.VISION
            else:
                assert self._ocr is not None
                ocr_text = self._ocr(images_b64)
                if not ocr_text.strip():
                    raise RoutingError("El OCR no reconoció texto en la captura.")
                messages = challenge_messages(
                    instruction, ocr_text=ocr_text, language=self._language()
                )
                task = Task.CHALLENGE
            result = self._engine.generate(
                task,
                messages,
                on_chunk=on_chunk,
                should_abort=self._cancel.is_set,
                on_notice=on_notice,
                temperature=0.2,
            )
            return ChallengeResult(result.text, method, result.model_label)
        except RoutingCancelled:
            raise
        finally:
            self._busy.release()
