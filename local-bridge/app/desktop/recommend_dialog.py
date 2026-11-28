"""Ventana «Recomendar configuración»: evaluación del equipo y aplicación.

1. El usuario elige el nivel de consumo en tarjetas seleccionables. Cada
   tarjeta muestra, tras analizar el equipo, la misma clasificación que el
   catálogo de modelos (``model_evaluator``): 🟢 ÓPTIMO, 🟡 COMPATIBLE,
   🟠 EXIGENTE o 🔴 NO COMPATIBLE para lo que ese nivel recomendaría.
2. Se detecta el hardware real y se evalúan modelos instalados y del
   catálogo (``compat/advisor.py``) en un hilo aparte.
3. Cada funcionalidad se presenta en una tarjeta legible (sin tablas que
   corten el texto): elección, clasificación, requisitos, velocidad,
   alternativa y lo que habría que descargar.
4. **Aplicar** calcula qué falta, pide un consentimiento explícito con
   tamaño y recursos, guarda una copia de la configuración, aplica lo que
   ya está disponible y descarga el resto con el ``DownloadManager`` común
   (progreso real). Cada parte se activa solo cuando su descarga termina y
   se verifica; si algo falla se informa y «Reintentar» vuelve a pedir
   únicamente lo que falló. Cancelar el consentimiento no inicia nada.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..compat.advisor import FEATURES, PROFILES, Recommendation, recommend_all
from ..config import Config
from ..copilot.settings import CopilotSettings, SettingsRepository
from ..model_evaluator import (
    TIER_ALTO,
    TIER_IMPOSIBLE,
    TIER_PERFECTO,
    TIER_REGULAR,
    TIER_SIN_EVALUAR,
)
from ..ollama_client import OllamaClient
from ..ollama_pull import COMPLETED, PullSnapshot, format_bytes
from .brand import LogoLabel
from .downloads import KIND_OLLAMA, KIND_WHISPER, DownloadJob, DownloadManager
from .ui_kit import (
    DIALOG_QSS,
    ChoiceCard,
    DownloadProgress,
    TierChip,
    card,
    fit_to_screen,
    scrollable,
    section,
    wrap_label,
)

log = logging.getLogger("unnexor.desktop.recommend")

BACKUP_NAME = "config_backup.json"
PROFILE_HELP = {
    "low": "Modelos ligeros: poca RAM y VRAM, menos calor y batería. Respuestas "
    "algo más simples.",
    "medium": "Equilibrio entre calidad, velocidad y consumo. Recomendado para "
    "la mayoría de equipos.",
    "high": "Los modelos más capaces que tu equipo puede ejecutar con margen. "
    "Más memoria y más espera por respuesta.",
}
PROFILE_CONSUMPTION = {
    "low": "Consumo bajo",
    "medium": "Consumo moderado",
    "high": "Consumo alto",
}
MAIN_KEYS = ("ollama_model", "ollama_temperature", "ollama_extra_options")
TASKS_FOR = {
    "conversation": ("answer",),
    "translation": ("translation", "summary"),
    "vision": ("vision", "challenge"),
}
TIER_RANK = {
    TIER_PERFECTO: 0,
    TIER_REGULAR: 1,
    TIER_ALTO: 2,
    TIER_IMPOSIBLE: 3,
    TIER_SIN_EVALUAR: -1,
}
RANK_LABEL = {
    TIER_PERFECTO: "rendimiento fluido",
    TIER_REGULAR: "rendimiento aceptable",
    TIER_ALTO: "exigente para tu equipo",
    TIER_IMPOSIBLE: "no recomendado",
}


def profile_tier(recs: list[Recommendation]) -> str:
    """Clasificación de un nivel = la peor de sus modelos de Ollama."""
    tiers = [r.tier for r in recs if r.engine == "Ollama" and r.available]
    if not tiers:
        return TIER_SIN_EVALUAR
    return max(tiers, key=lambda t: TIER_RANK.get(t, -1))


class _AnalysisWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        ollama: OllamaClient,
        profile: str,
        measured: dict[str, Any],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ollama = ollama
        self._profile = profile
        self._measured = measured

    def run(self) -> None:
        try:
            from .. import ocr_manager
            from ..hardware import detect, gpu_acceleration_active
            from ..model_catalog import recommended_catalog
            from ..stt.recommend import recommend as stt_recommend

            info = detect()
            try:
                info.ollama_gpu_active = gpu_acceleration_active(self._ollama)
            except Exception:
                info.ollama_gpu_active = None
            hw = info.to_dict()
            installed: list[dict[str, Any]] = []
            try:
                for entry in self._ollama.installed_models_full():
                    caps: list[str] = []
                    try:
                        caps = self._ollama.show(entry.get("name", "")).get(
                            "capabilities", []
                        )
                    except Exception:
                        pass
                    details = entry.get("details") or {}
                    installed.append(
                        {
                            "name": entry.get("name", ""),
                            "size_bytes": entry.get("size"),
                            "parameter_size": details.get("parameter_size", ""),
                            "capabilities": caps,
                        }
                    )
            except Exception as exc:
                log.info("Sin modelos de Ollama para evaluar: %s", exc)
            try:
                from ..stt import faster_whisper_engine as fw

                stt_installed = set(fw.installed_models())
                cuda = fw.cuda_devices() > 0
            except Exception:
                stt_installed, cuda = set(), False
            measured_stt = {
                k[4:]: float(v["call_s"])
                for k, v in self._measured.items()
                if k.startswith("stt:") and isinstance(v, dict) and v.get("call_s")
            }
            catalog = recommended_catalog()
            ocr = ocr_manager.available_engines()
            results: dict[str, dict[str, Any]] = {}
            for profile in PROFILES:
                stt = stt_recommend(
                    hw,
                    preference={"low": "speed", "medium": "balanced", "high": "quality"}[
                        profile
                    ],
                    installed=stt_installed,
                    cuda_available=cuda,
                    measured_call_s=measured_stt,
                )
                results[profile] = recommend_all(
                    hw,
                    profile=profile,
                    installed=installed,
                    catalog=catalog,
                    stt_recs=stt,
                    ocr_available=ocr,
                    measured=self._measured,
                )
            result = results[self._profile]
            result["hardware"] = hw
            result["installed_names"] = [m["name"] for m in installed]
            result["profile_tiers"] = {
                p: profile_tier(r["recommendations"]) for p, r in results.items()
            }
            self.done.emit(result)
        except Exception as exc:  # nunca tumbar la GUI
            log.exception("Fallo al recomendar configuración")
            self.failed.emit(str(exc))


def hardware_html(hw: dict[str, Any], accel: list[dict[str, str]]) -> str:
    """Resumen legible del equipo; lo que no se detecta se indica."""

    def val(value: Any, suffix: str = "") -> str:
        return (
            f"{value}{suffix}" if value not in (None, "", 0) else "<i>no detectado</i>"
        )

    backends = hw.get("backends") or {}
    cpu_flags = [
        name.upper()
        for name in ("avx", "avx2", "avx512")
        if (backends.get("cpu") or {}).get(name)
    ]
    ram_total = (hw.get("ram_total_mb") or 0) / 1024
    ram_free = (hw.get("ram_available_mb") or 0) / 1024
    lines = [
        f"<b>Procesador:</b> {val((hw.get('cpu_name') or '').strip())} · "
        f"{val(hw.get('cpu_cores'))} núcleos / {val(hw.get('cpu_threads'))} hilos"
        + (f" · {', '.join(cpu_flags)}" if cpu_flags else ""),
        f"<br><b>Memoria RAM:</b> {ram_total:.1f} GB en total · {ram_free:.1f} GB "
        f"disponibles · reservable para modelos ≈ {max(0.0, ram_free - 1):.1f} GB",
        f"<br><b>Almacenamiento</b> ({val(hw.get('disk_path'))}): "
        f"{val(hw.get('disk_free_gb'), ' GB')} libres de "
        f"{val(hw.get('disk_total_gb'), ' GB')}",
        f"<br><b>Sistema:</b> {val(hw.get('os_name'))}",
    ]
    if not accel:
        lines.append(
            "<br><b>Tarjeta gráfica:</b> <i>no se detectó ninguna GPU; los modelos "
            "se ejecutarán en la CPU.</i>"
        )
    for row in accel:
        lines.append(
            f"<br><b>Tarjeta gráfica:</b> {row['gpu']} ({row['vendor']}, VRAM "
            f"{row['vram']}) · compatible: {row['compatible']} · controladores: "
            f"{row['drivers']} · usada por Ollama: {row['used']}"
        )
    return "".join(lines)


# ---------------------------------------------------------------- plan
@dataclass
class Change:
    """Cambio de configuración asociado a una recomendación."""

    rec: Recommendation
    main: dict[str, Any] = field(default_factory=dict)
    pinned: dict[str, str] = field(default_factory=dict)
    temperatures: dict[str, float] = field(default_factory=dict)
    stt_engine: str = ""
    summary: str = ""
    job: Optional[DownloadJob] = None


@dataclass
class Plan:
    immediate: list[Change] = field(default_factory=list)
    downloads: list[Change] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    @property
    def download_bytes(self) -> int:
        return sum(c.job.expected_bytes for c in self.downloads if c.job is not None)

    def is_empty(self) -> bool:
        return not self.immediate and not self.downloads


class RecommendationCard(QWidget):
    """Tarjeta legible de una recomendación (sustituye a la tabla)."""

    def __init__(self, rec: Recommendation, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        frame = card(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        grid = QGridLayout(frame)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        title = wrap_label(f"<b>{rec.title}</b>")
        grid.addWidget(title, 0, 0)
        grid.addWidget(TierChip(rec.tier), 0, 1, Qt.AlignmentFlag.AlignRight)
        choice = wrap_label(
            f"<span style='font-size:15px'>{rec.choice}</span>"
            f" <span style='color:#8a93a5'>· {rec.engine}</span>"
        )
        grid.addWidget(choice, 1, 0, 1, 2)
        details = []
        if rec.requirements:
            details.append(f"<b>Requisitos:</b> {rec.requirements}")
        if rec.speed:
            details.append(f"<b>Velocidad esperada:</b> {rec.speed} ({rec.confidence})")
        if rec.quality:
            details.append(f"<b>Calidad:</b> {rec.quality}")
        if rec.alternative:
            details.append(f"<b>Alternativa más ligera:</b> {rec.alternative}")
        if rec.download_kind in (KIND_OLLAMA, KIND_WHISPER):
            size = (
                f" (~{format_bytes(rec.download_bytes)})" if rec.download_bytes else ""
            )
            details.append(
                f"<b>Requiere descarga:</b> {rec.download_name}{size} — se hará "
                "automáticamente al aplicar, con tu permiso."
            )
        elif rec.download_kind == "manual" and rec.download:
            details.append(
                f"<b>No incluido en esta edición:</b> {rec.download} (instalación manual)."
            )
        elif rec.available:
            details.append("<b>Estado:</b> instalado, listo para usar.")
        if rec.reasons:
            details.append(
                "<span style='color:#8a93a5'>" + " ".join(rec.reasons[:3]) + "</span>"
            )
        body = wrap_label("<br>".join(details))
        body.setTextFormat(Qt.TextFormat.RichText)
        grid.addWidget(body, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        self.rec = rec


class RecommendationDialog(QDialog):
    """Evalúa el equipo, propone y aplica una configuración por funcionalidad."""

    def __init__(
        self,
        cfg: Config,
        ollama: OllamaClient,
        *,
        apply_main: Callable[[dict[str, Any]], None] | None = None,
        copilot_settings: Callable[[], CopilotSettings] | None = None,
        save_copilot: Callable[[CopilotSettings], None] | None = None,
        downloads: DownloadManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recomendar configuración — UNnexor")
        self.setStyleSheet(DIALOG_QSS)
        fit_to_screen(self, QSize(1060, 820), QSize(720, 540), anchor=parent)
        self._cfg = cfg
        self._ollama = ollama
        self._apply_main = apply_main
        repo = SettingsRepository()
        self._copilot_settings = copilot_settings or repo.load
        self._save_copilot = save_copilot or repo.save
        self._downloads = downloads
        if self._downloads is None and ollama is not None:
            self._downloads = DownloadManager.instance(ollama)
        self._result: dict[str, Any] | None = None
        self._worker: _AnalysisWorker | None = None
        self._rec_cards: list[RecommendationCard] = []
        self._pending: dict[str, Change] = {}  # clave de descarga -> cambio
        self._failed: dict[str, Change] = {}
        self._progress_rows: dict[str, DownloadProgress] = {}
        self._profile = "medium"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        content = QWidget()
        content.setObjectName("pageRoot")
        body = QVBoxLayout(content)
        body.setContentsMargins(22, 18, 22, 18)
        body.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(LogoLabel(22))
        title = QLabel("Recomendar configuración")
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        body.addLayout(header)
        body.addWidget(
            wrap_label(
                "UNnexor analiza tu equipo real y propone qué modelo usar en cada "
                "función. Elige cuánto quieres que consuma y pulsa «Aplicar»: lo que "
                "falte se descargará automáticamente después de que lo autorices.",
                "muted",
            )
        )

        body.addWidget(section("1 · Nivel de consumo"))
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._profile_cards: dict[str, ChoiceCard] = {}
        for key, label in PROFILES.items():
            choice = ChoiceCard(
                label,
                PROFILE_HELP[key],
                tier=TIER_SIN_EVALUAR,
                tier_text=f"{PROFILE_CONSUMPTION[key]} · sin analizar",
            )
            choice.clicked.connect(lambda k=key: self._select_profile(k))
            self._profile_cards[key] = choice
            cards_row.addWidget(choice, 1)
        body.addLayout(cards_row)
        self._profile_cards["medium"].set_selected(True)

        body.addWidget(section("2 · Tu equipo"))
        hw_card = card()
        hw_layout = QVBoxLayout(hw_card)
        hw_layout.setContentsMargins(14, 12, 14, 12)
        self._hardware = wrap_label("Analizando tu equipo…")
        self._hardware.setTextFormat(Qt.TextFormat.RichText)
        hw_layout.addWidget(self._hardware)
        body.addWidget(hw_card)

        body.addWidget(section("3 · Recomendación por función"))
        self._recs_box = QVBoxLayout()
        self._recs_box.setSpacing(8)
        body.addLayout(self._recs_box)

        self._install_title = section("4 · Instalación")
        self._install_title.setVisible(False)
        body.addWidget(self._install_title)
        self._install_box = QVBoxLayout()
        self._install_box.setSpacing(8)
        body.addLayout(self._install_box)
        body.addStretch(1)
        root.addWidget(scrollable(content), 1)

        footer = QWidget()
        footer.setStyleSheet("background: #0f1116; border-top: 1px solid #262b36;")
        buttons = QHBoxLayout(footer)
        buttons.setContentsMargins(22, 10, 22, 10)
        self._status = wrap_label("", "muted")
        self._retry = QPushButton("Reintentar descargas fallidas")
        self._retry.setVisible(False)
        self._retry.clicked.connect(self.retry_failed)
        self._restore = QPushButton("Restaurar configuración anterior")
        self._restore.setEnabled(self._backup_path().is_file())
        self._restore.clicked.connect(self.restore)
        self._apply = QPushButton("Aplicar…")
        self._apply.setProperty("kind", "primary")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self.apply)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.reject)
        buttons.addWidget(self._status, 1)
        for button in (self._retry, self._restore, self._apply, close):
            buttons.addWidget(button)
        root.addWidget(footer)

        if self._downloads is not None:
            self._downloads.jobProgress.connect(self._on_job_progress)
            self._downloads.jobFinished.connect(self._on_job_finished)
        if ollama is not None:
            self.analyze()

    # ------------------------------------------------------------------
    def profile(self) -> str:
        return self._profile

    def _select_profile(self, key: str) -> None:
        if key == self._profile and self._result is not None:
            return
        self._profile = key
        for name, choice in self._profile_cards.items():
            choice.set_selected(name == key)
        if self._ollama is not None:
            self.analyze()

    def analyze(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._apply.setEnabled(False)
        self._status.setText("Analizando el equipo y los modelos…")
        measured = dict(self._copilot_settings().benchmarks)
        self._worker = _AnalysisWorker(self._ollama, self.profile(), measured, self)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_failed(self, message: str) -> None:
        self._status.setText(f"No se pudo analizar el equipo: {message}")

    def _on_done(self, result: Any) -> None:
        if (
            self._ollama is not None
            and result.get("profile")
            and result["profile"] != self._profile
        ):
            self.analyze()  # el usuario cambió de nivel durante el análisis
            return
        profile = result.get("profile")
        if profile in self._profile_cards and profile != self._profile:
            self._profile = profile
            for name, choice in self._profile_cards.items():
                choice.set_selected(name == profile)
        self._result = result
        self._hardware.setText(
            hardware_html(result["hardware"], result["acceleration"])
        )
        for key, tier in (result.get("profile_tiers") or {}).items():
            if key in self._profile_cards:
                self._profile_cards[key].set_tier(
                    tier,
                    f"{PROFILE_CONSUMPTION[key]} · {RANK_LABEL.get(tier, 'sin evaluar')}",
                )
        for old in self._rec_cards:
            old.deleteLater()
        self._rec_cards = []
        recs: list[Recommendation] = result["recommendations"]
        for rec in recs:
            widget = RecommendationCard(rec)
            self._rec_cards.append(widget)
            self._recs_box.addWidget(widget)
        plan = self._plan()
        self._apply.setEnabled(not plan.is_empty())
        extra = (
            f" Al aplicar se descargarán {len(plan.downloads)} componentes "
            f"(~{format_bytes(plan.download_bytes)})."
            if plan.downloads
            else ""
        )
        self._status.setText(
            f"Nivel «{result['profile_label']}» analizado. Nunca se recomiendan "
            f"modelos {' ni '.join(result['never_recommended'])}.{extra}"
        )

    # ------------------------------------------------------------------ plan
    def _change_for(self, rec: Recommendation) -> Optional[Change]:
        change = Change(rec)
        if rec.engine == "Ollama":
            if rec.feature == "chat":
                extra = dict(self._cfg.ollama_extra_options or {})
                for key in ("top_p", "top_k", "repeat_penalty", "num_ctx"):
                    if key in rec.params:
                        extra[key] = rec.params[key]
                change.main = {
                    "ollama_model": rec.choice,
                    "ollama_temperature": rec.params["temperature"],
                    "ollama_extra_options": extra,
                }
                change.summary = (
                    f"Panel principal: modelo {self._cfg.ollama_model} → {rec.choice}; "
                    f"temperatura {rec.params['temperature']}; contexto "
                    f"{rec.params.get('num_ctx', '—')}."
                )
            else:
                for task in TASKS_FOR.get(rec.feature, ()):
                    change.pinned[task] = f"ollama:{rec.choice}"
                    change.temperatures[task] = rec.params["temperature"]
                change.summary = (
                    f"Copiloto · {FEATURES[rec.feature]}: {rec.choice} "
                    f"(temperatura {rec.params['temperature']})."
                )
            return change
        if rec.feature == "stt":
            from ..stt.catalog import LOCAL_MODELS

            for spec in LOCAL_MODELS:
                if spec.label == rec.choice:
                    change.stt_engine = spec.key
            change.summary = f"Copiloto · transcripción: {rec.choice}."
            return change
        if rec.feature == "documents" and rec.download_kind == KIND_OLLAMA:
            change.summary = f"Documentos del perfil: {rec.choice}."
            return change
        return None

    def _plan(self) -> Plan:
        assert self._result is not None
        plan = Plan()
        for rec in self._result["recommendations"]:
            if not rec.available:
                continue
            if rec.download_kind == "manual":
                plan.manual.append(f"{FEATURES[rec.feature]}: {rec.download}")
                continue
            change = self._change_for(rec)
            if change is None:
                continue
            if rec.download_kind in (KIND_OLLAMA, KIND_WHISPER) and rec.download_name:
                change.job = DownloadJob(
                    rec.download_kind,
                    rec.download_name,
                    label=rec.choice,
                    expected_bytes=rec.download_bytes,
                )
                plan.downloads.append(change)
            else:
                plan.immediate.append(change)
        return plan

    def _backup_path(self):
        return paths.data_dir() / BACKUP_NAME

    # ---------------------------------------------------------------- aplicar
    def apply(self) -> None:
        if self._result is None:
            return
        plan = self._plan()
        can_download = self._downloads is not None
        if not can_download and plan.downloads:
            plan.manual.extend(
                f"{c.summary} (requiere descargar {c.job.name}; Ollama no disponible)"
                for c in plan.downloads
                if c.job is not None
            )
            plan.downloads = []
        if plan.is_empty():
            QMessageBox.information(
                self,
                "Nada que aplicar",
                "La configuración recomendada ya está aplicada o requiere pasos "
                "manuales:\n· " + "\n· ".join(plan.manual or ["—"]),
            )
            return
        text = ""
        if plan.immediate:
            text += "Se aplicará ahora:\n· " + "\n· ".join(
                c.summary for c in plan.immediate
            )
        if plan.downloads:
            free = (self._result.get("hardware") or {}).get("disk_free_gb")
            text += (
                ("\n\n" if text else "")
                + "Se DESCARGARÁ e instalará (fuente oficial de Ollama / Hugging Face):\n· "
                + "\n· ".join(
                    f"{c.job.title} (~{format_bytes(c.job.expected_bytes)})"
                    for c in plan.downloads
                    if c.job is not None
                )
                + f"\nTotal aproximado: {format_bytes(plan.download_bytes)} de disco"
                + (f" (tienes {free} GB libres)" if free else "")
                + ". Usará tu conexión a Internet mientras dure y, al usarlos, la "
                "memoria indicada en cada recomendación. Cada parte se activa cuando "
                "su descarga termina y se verifica."
            )
        if plan.manual:
            text += "\n\nNo se aplica (requiere una acción manual):\n· " + "\n· ".join(
                plan.manual
            )
        text += "\n\nSe guardará una copia para poder restaurar la configuración actual."
        text += "\n\n¿Autorizas estos cambios" + (
            " y descargas?" if plan.downloads else "?"
        )
        answer = QMessageBox.question(
            self,
            "Confirmar configuración recomendada",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._status.setText("No se aplicó nada: cancelaste la confirmación.")
            return
        self._write_backup()
        for change in plan.immediate:
            self._apply_change(change)
        if plan.downloads:
            self._start_downloads(plan.downloads)
        else:
            self._status.setText("Configuración aplicada. Puedes restaurar la anterior.")

    def _write_backup(self) -> None:
        settings = self._copilot_settings()
        backup = {
            "main": {k: getattr(self._cfg, k) for k in MAIN_KEYS},
            "copilot": {
                "pinned": dict(settings.pinned),
                "task_temperature": dict(settings.task_temperature),
                "stt_engine": settings.stt_engine,
            },
        }
        path = self._backup_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), "utf-8")
        self._restore.setEnabled(True)

    def _apply_change(self, change: Change) -> None:
        if change.main and self._apply_main is not None:
            self._apply_main(change.main)
        if change.pinned or change.temperatures or change.stt_engine:
            settings = self._copilot_settings()
            settings.pinned.update(change.pinned)
            settings.task_temperature.update(change.temperatures)
            if change.stt_engine:
                settings.stt_engine = change.stt_engine
            self._save_copilot(settings)
        log.info("Recomendación aplicada: %s", change.summary)

    # ------------------------------------------------------------- descargas
    def _start_downloads(self, changes: list[Change]) -> None:
        assert self._downloads is not None
        self._install_title.setVisible(True)
        self._retry.setVisible(False)
        for change in changes:
            job = change.job
            if job is None:
                continue
            self._pending[job.key] = change
            self._failed.pop(job.key, None)
            row = self._progress_rows.get(job.key)
            if row is None:
                box = card()
                layout = QVBoxLayout(box)
                layout.setContentsMargins(14, 10, 14, 10)
                layout.addWidget(wrap_label(f"<b>{job.title}</b> · {change.summary}"))
                row = DownloadProgress()
                layout.addWidget(row)
                self._install_box.addWidget(box)
                self._progress_rows[job.key] = row
            row.apply(PullSnapshot(job.key, "preparing", "", 0, 0, None, None, 0, 0))
            self._downloads.enqueue(job)
        self._update_install_status()

    def _on_job_progress(self, snapshot: PullSnapshot) -> None:
        row = self._progress_rows.get(snapshot.model)
        if row is not None and snapshot.model in self._pending:
            row.apply(snapshot)

    def _on_job_finished(self, snapshot: PullSnapshot) -> None:
        change = self._pending.pop(snapshot.model, None)
        row = self._progress_rows.get(snapshot.model)
        if row is not None and change is not None:
            row.apply(snapshot)
        if change is None:
            return
        if snapshot.phase == COMPLETED:
            self._apply_change(change)
        else:
            self._failed[snapshot.model] = change
        self._update_install_status()

    def _update_install_status(self) -> None:
        if self._pending:
            self._status.setText(
                f"Instalando {len(self._pending)} componente(s)… Puedes seguir usando "
                "UNnexor; cada parte se activa al terminar su descarga."
            )
            return
        if self._failed:
            self._retry.setVisible(True)
            self._status.setText(
                f"⚠ {len(self._failed)} descarga(s) no terminaron. Lo completado ya "
                "está aplicado; «Reintentar» pide solo lo que falló."
            )
            return
        if self._progress_rows:
            self._status.setText(
                "✓ Todo instalado, verificado y aplicado. Puedes restaurar la "
                "configuración anterior cuando quieras."
            )

    def retry_failed(self) -> None:
        changes = list(self._failed.values())
        self._failed.clear()
        if changes:
            self._start_downloads(changes)

    # ------------------------------------------------------------- restaurar
    def restore(self) -> None:
        path = self._backup_path()
        try:
            backup = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Restaurar", f"No hay copia válida: {exc}")
            return
        answer = QMessageBox.question(
            self,
            "Restaurar",
            "¿Volver a la configuración guardada antes de aplicar la recomendación?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._apply_main is not None and backup.get("main"):
            self._apply_main(backup["main"])
        settings = self._copilot_settings()
        old = backup.get("copilot") or {}
        settings.pinned = dict(old.get("pinned") or {})
        settings.task_temperature = dict(old.get("task_temperature") or {})
        if old.get("stt_engine"):
            settings.stt_engine = old["stt_engine"]
        self._save_copilot(settings)
        self._status.setText("Configuración anterior restaurada.")

    # ---------------------------------------------------------------- cierre
    def reject(self) -> None:
        if self._pending:
            answer = QMessageBox.question(
                self,
                "Descargas en curso",
                f"Quedan {len(self._pending)} descarga(s) en curso. Si cierras, "
                "continuarán en segundo plano, pero su configuración no se aplicará "
                "hasta que vuelvas a pulsar «Aplicar».\n\n¿Cerrar de todos modos?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    def done(self, result: int) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)
        if self._downloads is not None:
            for signal, slot in (
                (self._downloads.jobProgress, self._on_job_progress),
                (self._downloads.jobFinished, self._on_job_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        if self._pending:
            log.info(
                "La ventana se cerró con %d descargas en curso: continúan en "
                "segundo plano, pero su configuración no se aplicará automáticamente.",
                len(self._pending),
            )
        super().done(result)
