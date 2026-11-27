"""Ventana «Recomendar configuración»: evaluación del equipo y propuestas.

1. El usuario elige el perfil de consumo (menor / medio / alto).
2. Se detecta el hardware real y se evalúan los modelos instalados y del
   catálogo (``compat/advisor.py``), en un hilo aparte.
3. Se muestran: componentes detectados (o «no detectado»), aceleración por
   GPU (detectada / compatible / controladores / usada en inferencia) y una
   recomendación por funcionalidad con motivos y parámetros.
4. «Aplicar…» enseña un resumen de qué cambia y pide confirmación; antes
   de aplicar se guarda una copia de la configuración para poder
   restaurarla. Solo se aplican modelos ya instalados: los que requieren
   descarga se indican y se descargan explícitamente desde «Modelos de IA».
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..compat.advisor import FEATURES, PROFILES, Recommendation, recommend_all
from ..config import Config
from ..copilot.settings import CopilotSettings, SettingsRepository
from ..ollama_client import OllamaClient
from .brand import LogoLabel

log = logging.getLogger("extdator.desktop.recommend")

BACKUP_NAME = "config_backup.json"
PROFILE_HELP = {
    "low": "Modelos ligeros, poca RAM/VRAM y menos carga para el equipo.",
    "medium": "Equilibrio entre calidad, velocidad y consumo.",
    "high": "Los modelos más capaces que tu equipo puede ejecutar con margen.",
}
MAIN_KEYS = ("ollama_model", "ollama_temperature", "ollama_extra_options")
TASKS_FOR = {
    "conversation": ("answer",),
    "translation": ("translation", "summary"),
    "vision": ("vision", "challenge"),
}


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
            stt = stt_recommend(
                hw,
                preference={"low": "speed", "medium": "balanced", "high": "quality"}[
                    self._profile
                ],
                installed=stt_installed,
                cuda_available=cuda,
                measured_call_s=measured_stt,
            )
            result = recommend_all(
                hw,
                profile=self._profile,
                installed=installed,
                catalog=recommended_catalog(),
                stt_recs=stt,
                ocr_available=ocr_manager.available_engines(),
                measured=self._measured,
            )
            result["hardware"] = hw
            result["installed_names"] = [m["name"] for m in installed]
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
        "<h3>Tu equipo</h3>",
        f"<b>Procesador:</b> {val((hw.get('cpu_name') or '').strip())} · "
        f"arquitectura {val(hw.get('cpu_arch'))} · {val(hw.get('cpu_cores'))} núcleos / "
        f"{val(hw.get('cpu_threads'))} hilos"
        + (f" · {', '.join(cpu_flags)}" if cpu_flags else ""),
        f"<br><b>Memoria RAM:</b> {ram_total:.1f} GB en total · {ram_free:.1f} GB "
        f"disponibles · {val(hw.get('ram_used_percent'), ' % en uso')} · "
        f"reservable para modelos ≈ {max(0.0, ram_free - 1):.1f} GB",
        f"<br><b>Almacenamiento</b> ({val(hw.get('disk_path'))}): "
        f"{val(hw.get('disk_total_gb'), ' GB')} en total · "
        f"{val(hw.get('disk_free_gb'), ' GB')} libres",
        f"<br><b>Sistema:</b> {val(hw.get('os_name'))}",
        "<h3>Tarjetas gráficas y aceleración</h3>",
    ]
    if not accel:
        lines.append(
            "<i>No se detectó ninguna GPU: los modelos se ejecutarán en la CPU.</i>"
        )
    for row in accel:
        lines.append(
            f"<b>{row['gpu']}</b> ({row['vendor']}, VRAM {row['vram']})<br>"
            f"&nbsp;· Detectada: {row['detected']} · Tecnología compatible: "
            f"{row['compatible']}<br>&nbsp;· Controladores/bibliotecas: "
            f"{row['drivers']} · Usada en la inferencia: {row['used']}<br>"
        )
    return "".join(lines)


class RecommendationDialog(QDialog):
    """Evalúa el equipo y propone una configuración por funcionalidad."""

    def __init__(
        self,
        cfg: Config,
        ollama: OllamaClient,
        *,
        apply_main: Callable[[dict[str, Any]], None] | None = None,
        copilot_settings: Callable[[], CopilotSettings] | None = None,
        save_copilot: Callable[[CopilotSettings], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recomendar configuración — UNnexor")
        self.resize(1080, 760)
        self.setStyleSheet(
            "QDialog { background: #12141a; color: #e8eaf2; }"
            "QLabel, QRadioButton { color: #e8eaf2; background: transparent; }"
            "QTableWidget, QTextBrowser { background: #0e1016; color: #e8eaf2;"
            " border: 1px solid #262b36; border-radius: 6px; }"
            "QHeaderView::section { background: #171a21; color: #8a93a5;"
            " border: none; padding: 4px; }"
            "QPushButton { background: #20242e; color: #e8eaf2;"
            " border: 1px solid #262b36; border-radius: 6px; padding: 6px 12px; }"
            "QPushButton:hover { border-color: #00e5ff; }"
            "QPushButton#primary { background: #2a2bd0; border-color: #3a2bff; }"
        )
        self._cfg = cfg
        self._ollama = ollama
        self._apply_main = apply_main
        repo = SettingsRepository()
        self._copilot_settings = copilot_settings or repo.load
        self._save_copilot = save_copilot or repo.save
        self._result: dict[str, Any] | None = None
        self._worker: _AnalysisWorker | None = None

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(LogoLabel(18))
        title = QLabel("<b>Recomendar configuración</b> · según tu equipo real")
        title.setStyleSheet("font-size: 15px;")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        profiles = QHBoxLayout()
        self._group = QButtonGroup(self)
        for index, (key, label) in enumerate(PROFILES.items()):
            radio = QRadioButton(f"{label}")
            radio.setToolTip(PROFILE_HELP[key])
            radio.setProperty("profile", key)
            self._group.addButton(radio, index)
            profiles.addWidget(radio)
            if key == "medium":
                radio.setChecked(True)
        self._analyze = QPushButton("🔎 Analizar equipo")
        self._analyze.setObjectName("primary")
        self._analyze.clicked.connect(self.analyze)
        profiles.addStretch(1)
        profiles.addWidget(self._analyze)
        layout.addLayout(profiles)
        self._profile_help = QLabel(PROFILE_HELP["medium"])
        self._profile_help.setStyleSheet("color: #8a93a5;")
        self._group.idToggled.connect(self._on_profile)
        layout.addWidget(self._profile_help)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._hardware = QTextBrowser()
        self._hardware.setHtml("<i>Pulsa «Analizar equipo» para empezar.</i>")
        splitter.addWidget(self._hardware)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            [
                "Funcionalidad",
                "Recomendado",
                "Compatibilidad",
                "Requisitos",
                "Velocidad esperada",
                "Alternativa más ligera",
                "Descarga / dependencia",
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._show_details)
        splitter.addWidget(self._table)
        self._details = QTextBrowser()
        splitter.addWidget(self._details)
        splitter.setSizes([200, 260, 200])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet("color: #8a93a5;")
        self._status.setWordWrap(True)
        self._apply = QPushButton("✅ Aplicar…")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self.apply)
        self._restore = QPushButton("↩ Restaurar configuración anterior")
        self._restore.setEnabled(self._backup_path().is_file())
        self._restore.clicked.connect(self.restore)
        close = QPushButton("Mantener mi configuración y cerrar")
        close.clicked.connect(self.reject)
        buttons.addWidget(self._status, 1)
        buttons.addWidget(self._restore)
        buttons.addWidget(self._apply)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------
    def profile(self) -> str:
        button = self._group.checkedButton()
        return str(button.property("profile")) if button else "medium"

    def _on_profile(self, _id: int, checked: bool) -> None:
        if checked:
            self._profile_help.setText(PROFILE_HELP[self.profile()])
            if self._result is not None:
                self.analyze()

    def analyze(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._analyze.setEnabled(False)
        self._apply.setEnabled(False)
        self._status.setText("Analizando el equipo y los modelos…")
        measured = dict(self._copilot_settings().benchmarks)
        self._worker = _AnalysisWorker(self._ollama, self.profile(), measured, self)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_failed(self, message: str) -> None:
        self._analyze.setEnabled(True)
        self._status.setText(f"No se pudo analizar el equipo: {message}")

    def _on_done(self, result: Any) -> None:
        self._result = result
        self._analyze.setEnabled(True)
        self._hardware.setHtml(
            hardware_html(result["hardware"], result["acceleration"])
        )
        recs: list[Recommendation] = result["recommendations"]
        self._table.setRowCount(len(recs))
        for row, rec in enumerate(recs):
            values = [
                rec.title,
                rec.choice,
                f"{rec.label} ({rec.confidence})",
                rec.requirements,
                rec.speed,
                rec.alternative,
                rec.download or "Instalado / sin descarga",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self._table.setItem(row, column, item)
        self._table.selectRow(0)
        self._apply.setEnabled(True)
        self._status.setText(
            f"Perfil «{result['profile_label']}». Nunca se recomiendan modelos "
            f"{' ni '.join(result['never_recommended'])} para tu equipo."
        )

    def _show_details(self) -> None:
        if self._result is None:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        rec: Recommendation = self._result["recommendations"][rows[0].row()]
        params = (
            "<br>".join(f"<code>{k}</code> = {v}" for k, v in rec.params.items()) or "—"
        )
        reasons = "".join(f"<li>{r}</li>" for r in rec.reasons) or "<li>—</li>"
        self._details.setHtml(
            f"<h3>{rec.title}</h3><b>Motor:</b> {rec.engine} · "
            f"<b>Elección:</b> {rec.choice} · <b>Calidad esperada:</b> "
            f"{rec.quality or '—'}<br><b>Motivos técnicos:</b><ul>{reasons}</ul>"
            f"<b>Parámetros recomendados:</b><br>{params}"
        )

    # ------------------------------------------------------------------ aplicar
    def _plan(self) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
        """(cambios del panel, cambios del copiloto, resumen, no aplicables)."""
        assert self._result is not None
        installed = {n.lower() for n in self._result["installed_names"]}
        main: dict[str, Any] = {}
        copilot: dict[str, Any] = {"pinned": {}, "task_temperature": {}}
        summary: list[str] = []
        skipped: list[str] = []
        for rec in self._result["recommendations"]:
            if rec.engine == "Ollama" and rec.available:
                if rec.choice.lower() not in installed:
                    skipped.append(
                        f"{FEATURES[rec.feature]}: {rec.choice} requiere descarga "
                        "(Modelos de IA → Descargar)."
                    )
                    continue
                if rec.feature == "chat":
                    extra = dict(self._cfg.ollama_extra_options or {})
                    for key in ("top_p", "top_k", "repeat_penalty", "num_ctx"):
                        if key in rec.params:
                            extra[key] = rec.params[key]
                    main = {
                        "ollama_model": rec.choice,
                        "ollama_temperature": rec.params["temperature"],
                        "ollama_extra_options": extra,
                    }
                    summary.append(
                        f"Panel principal: modelo {self._cfg.ollama_model} → "
                        f"{rec.choice}; temperatura {self._cfg.ollama_temperature} → "
                        f"{rec.params['temperature']}; contexto → {rec.params['num_ctx']}."
                    )
                else:
                    for task in TASKS_FOR.get(rec.feature, ()):
                        copilot["pinned"][task] = f"ollama:{rec.choice}"
                        copilot["task_temperature"][task] = rec.params["temperature"]
                    summary.append(
                        f"Copiloto · {FEATURES[rec.feature]}: {rec.choice} "
                        f"(temperatura {rec.params['temperature']})."
                    )
            elif rec.feature == "stt" and rec.available:
                if rec.download:
                    skipped.append(f"Voz: {rec.choice} requiere descarga.")
                else:
                    copilot["stt_choice"] = rec.choice
                    summary.append(f"Copiloto · transcripción: {rec.choice}.")
        return main, copilot, summary, skipped

    def _backup_path(self):
        return paths.data_dir() / BACKUP_NAME

    def apply(self) -> None:
        if self._result is None:
            return
        main, copilot, summary, skipped = self._plan()
        if not summary:
            QMessageBox.information(
                self,
                "Nada que aplicar",
                "Las recomendaciones requieren descargar modelos primero:\n· "
                + "\n· ".join(skipped),
            )
            return
        text = "Se cambiará:\n· " + "\n· ".join(summary)
        if skipped:
            text += "\n\nNo se aplica (requiere una acción tuya):\n· " + "\n· ".join(
                skipped
            )
        text += (
            "\n\nSe guardará una copia para poder restaurar la configuración actual."
        )
        answer = QMessageBox.question(self, "Confirmar cambios", text)
        if answer != QMessageBox.StandardButton.Yes:
            return
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
        if main and self._apply_main is not None:
            self._apply_main(main)
        settings.pinned.update(copilot["pinned"])
        settings.task_temperature.update(copilot["task_temperature"])
        stt_choice = copilot.get("stt_choice")
        if stt_choice:
            from ..stt.catalog import LOCAL_MODELS

            for spec in LOCAL_MODELS:
                if spec.label == stt_choice:
                    settings.stt_engine = spec.key
        self._save_copilot(settings)
        self._restore.setEnabled(True)
        self._status.setText("Configuración aplicada. Puedes restaurar la anterior.")

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
