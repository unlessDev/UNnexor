"""Secciones de configuración del copiloto: inicio, perfiles y
conocimiento, motores y modelos, privacidad."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...knowledge.profiles import ContextProfile, Detail, Tone
from ...providers.access import PLAN_DISCLAIMER, AccessTier
from ...providers.catalog import provider_catalog
from ...routing.router import Strategy, Task
from ...stt.catalog import ALL_MODELS, get_spec
from ...stt.factory import engine_status
from .common import hint, language_combo, select_data, title
from .controller import CopilotController
from .tasks import run_async


# ===================================================================== Inicio
class HomePage(QWidget):
    openMode = Signal(str)

    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        layout = QVBoxLayout(self)
        layout.addWidget(title("Copiloto en tiempo real"))
        layout.addWidget(
            hint(
                "Elige una modalidad. Todo funciona en tu equipo por defecto; los "
                "servicios externos solo se usan si los autorizas en 'Motores y "
                "modelos'."
            )
        )
        grid = QGridLayout()
        cards = [
            (
                "conversation",
                "🎧 Asistencia en conversaciones",
                "Detecta preguntas en una reunión o entrevista y sugiere respuestas con tu perfil.",
            ),
            (
                "translation",
                "🌐 Traducción en tiempo real",
                "Transcribe y traduce lo que se dice, con exportación y resumen.",
            ),
            (
                "challenges",
                "🧩 Desafíos en pantalla",
                "Captura un ejercicio o código visible y obtén una solución paso a paso.",
            ),
        ]
        for column, (key, name, text) in enumerate(cards):
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            heading = QLabel(name)
            heading.setStyleSheet("font-size: 14px; font-weight: 700;")
            body = hint(text)
            button = QPushButton("Abrir")
            button.setObjectName("primary")
            button.clicked.connect(lambda _c=False, k=key: self.openMode.emit(k))
            card_layout.addWidget(heading)
            card_layout.addWidget(body, 1)
            card_layout.addWidget(button)
            grid.addWidget(card, 0, column)
        layout.addLayout(grid)
        setup = QGroupBox("Configuración guiada")
        setup_layout = QVBoxLayout(setup)
        setup_layout.addWidget(
            hint(
                "Analiza tu equipo y elige automáticamente el motor de voz y la "
                "estrategia de modelos. No descarga nada sin preguntarte."
            )
        )
        self.auto = QPushButton("✨ Recomendar configuración")
        setup_layout.addWidget(self.auto)
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(220)
        setup_layout.addWidget(self.summary)
        layout.addWidget(setup)
        layout.addStretch(1)
        self.auto.clicked.connect(self.recommend)

    def recommend(self) -> None:
        self.auto.setEnabled(False)
        self.summary.setPlainText("Analizando el equipo…")

        def work() -> dict[str, Any]:
            from ...hardware import detect
            from ...stt import faster_whisper_engine as fw
            from ...stt.recommend import recommend

            hw = detect().to_dict()
            self._c.set_hardware(hw)
            connected = {
                p.id for p in provider_catalog() if not p.local and engine_has_key(p.id)
            }
            recs = recommend(
                hw,
                allow_remote=bool(self._c.consents.providers_with("remote_data")),
                connected=connected,
                installed=set(fw.installed_models()),
                cuda_available=fw.cuda_devices() > 0,
            )
            return {"hw": hw, "recs": recs, "routes": self._c.describe_routes()}

        def done(result: Any) -> None:
            self.auto.setEnabled(True)
            recs = result["recs"]
            hw = result["hw"]
            lines = [
                f"**Equipo:** {hw.get('cpu_name', '').strip()} · "
                f"{hw.get('cpu_cores')} núcleos · "
                f"{round((hw.get('ram_total_mb') or 0) / 1024)} GB RAM · "
                + ", ".join(
                    f"{g['name']} ({g.get('vram_total_mb', 0) // 1024} GB)"
                    for g in hw.get("gpus", [])
                )
            ]
            if recs:
                best = next((r for r in recs if r.ready), recs[0])
                s = self._c.settings
                s.stt_engine = best.spec.key
                if s.strategy == Strategy.SELECTED_ONLY.value:
                    s.strategy = Strategy.LOCAL_FIRST.value
                self._c.save_settings(s)
                lines.append(f"\n**Motor de voz elegido:** {best.spec.label}")
                lines += [f"- {r}" for r in best.reasons]
                lines += [f"- ⚠ {w}" for w in best.warnings]
                if not best.ready:
                    lines.append("\nDescárgalo o conéctalo en **Motores y modelos**.")
            lines.append("\n**Modelos de lenguaje por tarea:**")
            lines += [f"- {text}" for text in result["routes"].values()]
            self.summary.setMarkdown("\n".join(lines))

        def failed(message: str) -> None:
            self.auto.setEnabled(True)
            self.summary.setPlainText(f"No se pudo analizar el equipo: {message}")

        run_async(work, on_done=done, on_error=failed)


def engine_has_key(provider_id: str) -> bool:
    try:
        from ...providers.manager import get_key

        return bool(get_key(provider_id))
    except Exception:
        return False


# ============================================================ Perfiles + docs
class ProfilesPage(QWidget):
    profilesChanged = Signal()

    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        self._current: ContextProfile | None = None
        outer = QVBoxLayout(self)
        outer.addWidget(title("Perfiles y base de conocimiento"))
        outer.addWidget(
            hint(
                "El perfil personaliza las respuestas. Los documentos se procesan "
                "en tu equipo; solo se envían extractos a un servicio externo si "
                "lo permites en el perfil y en el proveedor."
            )
        )
        row = QHBoxLayout()
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._select)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        new = QPushButton("Nuevo")
        delete = QPushButton("Eliminar")
        delete.setObjectName("danger")
        buttons.addWidget(new)
        buttons.addWidget(delete)
        left.addLayout(buttons)
        row.addLayout(left, 1)

        form_box = QGroupBox("Perfil")
        form = QFormLayout(form_box)
        self.name = QLineEdit()
        self.role = QLineEdit()
        self.about = QPlainTextEdit()
        self.about.setPlaceholderText("Experiencia, formación, habilidades, proyectos…")
        self.goal = QLineEdit()
        self.company = QLineEdit()
        self.language = language_combo(include_auto=False)
        self.tone = QComboBox()
        for tone in Tone:
            self.tone.addItem(tone.label, tone.value)
        self.custom_tone = QLineEdit()
        self.custom_tone.setPlaceholderText("Solo para tono personalizado")
        self.detail = QComboBox()
        for detail in Detail:
            self.detail.addItem(detail.label, detail.value)
        self.glossary = QLineEdit()
        self.glossary.setPlaceholderText(
            "Términos que el reconocimiento de voz debe conocer"
        )
        self.allow_remote = QCheckBox(
            "Permitir enviar extractos de este perfil a servicios externos autorizados"
        )
        for label, widget in (
            ("Nombre", self.name),
            ("Rol o puesto", self.role),
            ("Experiencia", self.about),
            ("Objetivo", self.goal),
            ("Empresa / contraparte", self.company),
            ("Idioma de respuesta", self.language),
            ("Tono", self.tone),
            ("", self.custom_tone),
            ("Nivel de detalle", self.detail),
            ("Glosario", self.glossary),
            ("", self.allow_remote),
        ):
            form.addRow(label, widget)
        save = QPushButton("Guardar perfil")
        save.setObjectName("primary")
        activate = QPushButton("Usar en el copiloto")
        save_row = QHBoxLayout()
        save_row.addWidget(save)
        save_row.addWidget(activate)
        form.addRow("", self._wrap(save_row))
        row.addWidget(form_box, 2)
        outer.addLayout(row, 2)

        docs = QGroupBox("Documentos del perfil")
        docs_layout = QVBoxLayout(docs)
        self.docs = QListWidget()
        docs_layout.addWidget(self.docs, 1)
        doc_buttons = QHBoxLayout()
        add = QPushButton("Añadir archivos…")
        note = QPushButton("Añadir nota…")
        remove = QPushButton("Quitar")
        embed = QPushButton("Búsqueda semántica (embeddings)")
        for button in (add, note, remove, embed):
            doc_buttons.addWidget(button)
        doc_buttons.addStretch(1)
        docs_layout.addLayout(doc_buttons)
        self.doc_status = QLabel("")
        self.doc_status.setObjectName("statusLine")
        self.doc_status.setWordWrap(True)
        docs_layout.addWidget(self.doc_status)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Probar una búsqueda en los documentos…")
        search_row.addWidget(self.search)
        docs_layout.addLayout(search_row)
        self.results = QTextBrowser()
        self.results.setMaximumHeight(140)
        docs_layout.addWidget(self.results)
        outer.addWidget(docs, 2)

        new.clicked.connect(self.new_profile)
        delete.clicked.connect(self.delete_profile)
        save.clicked.connect(self.save_profile)
        activate.clicked.connect(self.activate_profile)
        add.clicked.connect(self.add_files)
        note.clicked.connect(self.add_note)
        remove.clicked.connect(self.remove_doc)
        embed.clicked.connect(self.embed)
        self.search.returnPressed.connect(self.try_search)
        self.allow_remote.toggled.connect(self._warn_remote)
        self.reload()

    @staticmethod
    def _wrap(layout: QHBoxLayout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def reload(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for profile in self._c.profiles.list():
            marker = " ★" if profile.id == self._c.settings.active_profile else ""
            item = QListWidgetItem(profile.name + marker)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.list.addItem(item)
            if profile.id == select:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._select()

    def _select(self, *_args: object) -> None:
        item = self.list.currentItem()
        profile = (
            self._c.profiles.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        )
        self._current = profile
        p = profile or ContextProfile(name="")
        self.name.setText(p.name)
        self.role.setText(p.role)
        self.about.setPlainText(p.about)
        self.goal.setText(p.goal)
        self.company.setText(p.company)
        select_data(self.language, p.language)
        select_data(self.tone, p.tone.value)
        self.custom_tone.setText(p.custom_tone)
        select_data(self.detail, p.detail.value)
        self.glossary.setText(p.glossary)
        self.allow_remote.blockSignals(True)
        self.allow_remote.setChecked(p.allow_remote_documents)
        self.allow_remote.blockSignals(False)
        self._load_docs()

    def _warn_remote(self, checked: bool) -> None:
        if checked:
            QMessageBox.information(
                self,
                "Documentos y servicios externos",
                "Con esta opción, los extractos relevantes de tu perfil y "
                "documentos pueden enviarse a los proveedores que tengan además "
                "el permiso 'documentos' en 'Motores y modelos'. Sin ese permiso "
                "seguirán usándose solo modelos locales.",
            )

    def new_profile(self) -> None:
        self.list.clearSelection()
        self._current = None
        self.list.setCurrentRow(-1)
        for widget in (
            self.name,
            self.role,
            self.goal,
            self.company,
            self.custom_tone,
            self.glossary,
        ):
            widget.clear()
        self.about.clear()
        self.allow_remote.setChecked(False)
        self.docs.clear()
        self.name.setFocus()

    def save_profile(self) -> ContextProfile | None:
        profile = self._current or ContextProfile(name="")
        profile.name = self.name.text().strip()
        profile.role = self.role.text().strip()
        profile.about = self.about.toPlainText().strip()
        profile.goal = self.goal.text().strip()
        profile.company = self.company.text().strip()
        profile.language = str(self.language.currentData() or "es")
        profile.tone = Tone(self.tone.currentData())
        profile.custom_tone = self.custom_tone.text().strip()
        profile.detail = Detail(self.detail.currentData())
        profile.glossary = self.glossary.text().strip()
        profile.allow_remote_documents = self.allow_remote.isChecked()
        try:
            self._c.profiles.save(profile)
        except ValueError as exc:
            QMessageBox.warning(self, "Perfil", str(exc))
            return None
        self._current = profile
        self.reload(select=profile.id)
        self.profilesChanged.emit()
        return profile

    def activate_profile(self) -> None:
        profile = self._current or self.save_profile()
        if profile is None:
            return
        s = self._c.settings
        s.active_profile = profile.id
        self._c.save_settings(s)
        self.reload(select=profile.id)
        self.profilesChanged.emit()

    def delete_profile(self) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self,
            "Eliminar perfil",
            f"¿Eliminar '{self._current.name}' y todos sus documentos indexados?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._c.knowledge.delete_profile(self._current.id)
        self._c.profiles.delete(self._current.id)
        if self._c.settings.active_profile == self._current.id:
            s = self._c.settings
            s.active_profile = ""
            self._c.save_settings(s)
        self._current = None
        self.reload()
        self.profilesChanged.emit()

    # ------------------------------------------------------------- docs
    def _load_docs(self) -> None:
        self.docs.clear()
        if self._current is None:
            return
        for doc in self._c.knowledge.list_documents(self._current.id):
            semantic = (
                f" · {doc.embedded_chunks}/{doc.chunks} vectores"
                if doc.embedded_chunks
                else ""
            )
            item = QListWidgetItem(
                f"{doc.name} ({doc.kind}, {doc.chunks} fragmentos{semantic})"
            )
            item.setData(Qt.ItemDataRole.UserRole, doc.id)
            self.docs.addItem(item)

    def _require_profile(self) -> ContextProfile | None:
        profile = self._current or self.save_profile()
        if profile is None:
            QMessageBox.information(self, "Documentos", "Guarda primero el perfil.")
        return profile

    def add_files(self) -> None:
        profile = self._require_profile()
        if profile is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Añadir documentos",
            "",
            "Documentos (*.pdf *.docx *.txt *.md *.markdown)",
        )
        if not files:
            return
        self.doc_status.setText("Procesando documentos…")

        def work() -> list[str]:
            messages = []
            for name in files:
                try:
                    info = self._c.knowledge.add_file(profile.id, Path(name))
                    messages.append(f"✓ {info.name}: {info.chunks} fragmentos")
                except Exception as exc:  # se informa por archivo
                    messages.append(
                        f"✗ {Path(name).name}: {getattr(exc, 'args', [exc])[0]}"
                    )
            return messages

        run_async(work, on_done=self._docs_done, on_error=self.doc_status.setText)

    def _docs_done(self, messages: Any) -> None:
        self.doc_status.setText("\n".join(messages))
        self._load_docs()

    def add_note(self) -> None:
        profile = self._require_profile()
        if profile is None:
            return
        title_text, ok = QInputDialog.getText(self, "Nota", "Título de la nota:")
        if not ok:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Nota", "Contenido:")
        if not ok or not text.strip():
            return
        try:
            self._c.knowledge.add_note(profile.id, title_text, text)
        except ValueError as exc:
            QMessageBox.warning(self, "Nota", str(exc))
        self._load_docs()

    def remove_doc(self) -> None:
        item = self.docs.currentItem()
        if item is not None:
            self._c.knowledge.remove(item.data(Qt.ItemDataRole.UserRole))
            self._load_docs()

    def embed(self) -> None:
        profile = self._current
        if profile is None:
            return
        self.doc_status.setText(
            "Calculando embeddings con Ollama (nomic-embed-text)… Si no está "
            "instalado, la búsqueda seguirá funcionando por palabras."
        )

        def done(count: Any) -> None:
            self.doc_status.setText(f"{count} fragmentos con búsqueda semántica.")
            self._load_docs()

        def failed(message: str) -> None:
            self.doc_status.setText(
                f"No se calcularon embeddings: {message} Instala el modelo con "
                "'ollama pull nomic-embed-text' o desde 'Modelos de IA'."
            )

        run_async(
            lambda: self._c.knowledge.embed_pending(profile.id),
            on_done=done,
            on_error=failed,
        )

    def try_search(self) -> None:
        profile = self._current
        if profile is None or not self.search.text().strip():
            return
        hits = self._c.knowledge.search(profile.id, self.search.text(), k=3)
        if not hits:
            self.results.setPlainText("Sin resultados.")
            return
        self.results.setMarkdown(
            "\n\n".join(f"**{h.source}** ({h.method})\n\n{h.text[:300]}…" for h in hits)
        )


# ============================================================ Motores y modelos
class _ProgressBridge(QObject):
    value = Signal(int)


class EnginesPage(QWidget):
    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.addWidget(title("Motores y modelos"))
        layout.addWidget(hint(PLAN_DISCLAIMER))

        voice = QGroupBox("Reconocimiento de voz")
        voice_form = QFormLayout(voice)
        self.stt = QComboBox()
        for spec in ALL_MODELS:
            tag = "local" if spec.is_local else spec.access.label.lower()
            suffix = " · deprecado" if spec.deprecated else ""
            self.stt.addItem(f"{spec.label} — {tag}{suffix}", spec.key)
        self.stt_language = language_combo(include_auto=True)
        self.partials = QCheckBox("Mostrar texto provisional mientras se habla")
        self.stt_status = QLabel("")
        self.stt_status.setWordWrap(True)
        self.download = QPushButton("Descargar modelo…")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        voice_form.addRow("Motor", self.stt)
        voice_form.addRow("Idioma", self.stt_language)
        voice_form.addRow("", self.partials)
        voice_form.addRow("Estado", self.stt_status)
        voice_form.addRow("", self.download)
        voice_form.addRow("", self.progress)
        layout.addWidget(voice)

        gen = QGroupBox("Modelos de lenguaje")
        gen_form = QFormLayout(gen)
        self.strategy = QComboBox()
        for strategy in Strategy:
            self.strategy.addItem(strategy.label, strategy.value)
        self.pins: dict[Task, QComboBox] = {}
        gen_form.addRow("Estrategia", self.strategy)
        for task in (Task.ANSWER, Task.TRANSLATION, Task.VISION, Task.SUMMARY):
            combo = QComboBox()
            combo.addItem("Automático", "")
            self.pins[task] = combo
            gen_form.addRow(task.label, combo)
        self.refresh_models = QPushButton("Actualizar lista de modelos")
        gen_form.addRow("", self.refresh_models)
        self.routes = QTextBrowser()
        self.routes.setMaximumHeight(130)
        gen_form.addRow("Decisión actual", self.routes)
        layout.addWidget(gen)

        remote = QGroupBox("Servicios externos: permisos y modelos")
        remote_layout = QVBoxLayout(remote)
        remote_layout.addWidget(
            hint(
                "Marca qué puede hacer cada servicio. 'Enviar datos' es necesario "
                "para usarlo; 'Costos' para servicios que pueden cobrar o "
                "consumir saldo; 'Documentos' para enviar extractos de tus "
                "documentos personales. Conecta las claves en el panel principal "
                "(⚙ → Proveedores de API)."
            )
        )
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Servicio", "Acceso", "Clave", "Enviar datos", "Costos", "Documentos"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(220)
        remote_layout.addWidget(self.table)
        models_row = QHBoxLayout()
        self.remote_provider = QComboBox()
        self.remote_models = QLineEdit()
        self.remote_models.setPlaceholderText(
            "Modelos separados por comas (p. ej. openai/gpt-oss-20b)"
        )
        load_remote = QPushButton("Listar modelos")
        save_remote = QPushButton("Guardar")
        models_row.addWidget(self.remote_provider)
        models_row.addWidget(self.remote_models, 1)
        models_row.addWidget(load_remote)
        models_row.addWidget(save_remote)
        remote_layout.addLayout(models_row)
        self.remote_list = QListWidget()
        self.remote_list.setMaximumHeight(120)
        remote_layout.addWidget(self.remote_list)
        layout.addWidget(remote)

        usage = QGroupBox(
            "Uso de servicios externos (últimos 30 días, solo contadores)"
        )
        usage_layout = QVBoxLayout(usage)
        self.usage = QTextBrowser()
        self.usage.setMaximumHeight(140)
        usage_layout.addWidget(self.usage)
        layout.addWidget(usage)
        layout.addStretch(1)

        self.stt.currentIndexChanged.connect(self._stt_changed)
        self.stt_language.currentIndexChanged.connect(self._save_voice)
        self.partials.toggled.connect(self._save_voice)
        self.download.clicked.connect(self.download_model)
        self.strategy.currentIndexChanged.connect(self._save_generation)
        for combo in self.pins.values():
            combo.currentIndexChanged.connect(self._save_generation)
        self.refresh_models.clicked.connect(self.load_candidates)
        self.table.itemChanged.connect(self._consent_changed)
        self.remote_provider.currentIndexChanged.connect(self._show_remote_models)
        load_remote.clicked.connect(self.list_remote_models)
        save_remote.clicked.connect(self.save_remote_models)
        self.remote_list.itemDoubleClicked.connect(self._add_remote_model)
        self.reload()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        s = self._c.settings
        for widget in (self.stt, self.stt_language, self.partials, self.strategy):
            widget.blockSignals(True)
        select_data(self.stt, s.stt_engine)
        select_data(self.stt_language, s.stt_language)
        self.partials.setChecked(s.show_partials)
        select_data(self.strategy, s.strategy)
        for widget in (self.stt, self.stt_language, self.partials, self.strategy):
            widget.blockSignals(False)
        self._update_stt_status()
        self._fill_consents()
        self._fill_usage()
        self.load_candidates()

    def _update_stt_status(self) -> None:
        key = str(self.stt.currentData())
        spec = get_spec(key)
        status = engine_status(spec)
        text = "✅ Listo" if status.ready else f"⚠ {status.reason}"
        details = [spec.notes, spec.price_note, spec.limits_note, spec.deprecated]
        self.stt_status.setText(text + "\n" + "\n".join(d for d in details if d))
        self.download.setVisible(
            spec.is_local and not status.ready and "descargado" in status.reason
        )

    def _stt_changed(self) -> None:
        key = str(self.stt.currentData())
        spec = get_spec(key)
        if (
            not spec.is_local
            and spec.provider_id not in self._c.consents.providers_with("remote_data")
        ):
            QMessageBox.information(
                self,
                "Servicio externo",
                f"{spec.label} envía el audio a {spec.provider_id}. Autoriza "
                "'Enviar datos' para ese servicio en la tabla de abajo antes de "
                "usarlo.",
            )
        self._save_voice()

    def _save_voice(self) -> None:
        s = self._c.settings
        s.stt_engine = str(self.stt.currentData())
        s.stt_language = str(self.stt_language.currentData() or "")
        s.show_partials = self.partials.isChecked()
        self._c.save_settings(s)
        self._update_stt_status()

    def download_model(self) -> None:
        from ...stt import faster_whisper_engine as fw

        spec = get_spec(str(self.stt.currentData()))
        answer = QMessageBox.question(
            self,
            "Descargar modelo de voz",
            f"Se descargarán ~{spec.download_mb} MB desde Hugging Face "
            f"(repositorio oficial de faster-whisper) a\n{fw.models_root()}.\n\n"
            "¿Continuar?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.download.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, int(spec.download_mb or 0) or 0)
        # El progreso llega desde un hilo de trabajo: se reenvía por señal
        # (conexión en cola) para no tocar widgets fuera del hilo de la GUI.
        bridge = _ProgressBridge(self)
        bridge.value.connect(self.progress.setValue)

        def work() -> str:
            fw.download_model(
                spec.model, on_progress=lambda mb: bridge.value.emit(int(mb))
            )
            return spec.label

        def done(label: Any) -> None:
            self.download.setEnabled(True)
            self.progress.setVisible(False)
            self._update_stt_status()
            QMessageBox.information(self, "Modelo de voz", f"{label} listo.")

        def failed(message: str) -> None:
            self.download.setEnabled(True)
            self.progress.setVisible(False)
            QMessageBox.warning(self, "Modelo de voz", message)

        run_async(work, on_done=done, on_error=failed)

    # -------------------------------------------------------- generación
    def load_candidates(self) -> None:
        self.routes.setPlainText("Consultando modelos…")

        def work() -> Any:
            self._c.engine.invalidate()
            return self._c.engine.candidates(), self._c.describe_routes()

        def done(result: Any) -> None:
            candidates, routes = result
            for task, combo in self.pins.items():
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("Automático", "")
                for cand in candidates:
                    if task.required_capabilities <= cand.capabilities:
                        combo.addItem(cand.label, cand.key)
                select_data(combo, self._c.settings.pinned.get(task.value, ""))
                combo.blockSignals(False)
            self.routes.setMarkdown("\n".join(f"- {text}" for text in routes.values()))

        run_async(work, on_done=done, on_error=lambda m: self.routes.setPlainText(m))

    def _save_generation(self) -> None:
        s = self._c.settings
        s.strategy = str(self.strategy.currentData())
        s.pinned = {
            task.value: str(combo.currentData())
            for task, combo in self.pins.items()
            if combo.currentData()
        }
        self._c.save_settings(s)
        run_async(
            self._c.describe_routes,
            on_done=lambda r: self.routes.setMarkdown(
                "\n".join(f"- {text}" for text in r.values())
            ),
        )

    # -------------------------------------------------------- servicios
    def _fill_consents(self) -> None:
        providers = [p for p in provider_catalog() if not p.local]
        self.table.blockSignals(True)
        self.table.setRowCount(len(providers))
        self.remote_provider.blockSignals(True)
        self.remote_provider.clear()
        for row, info in enumerate(providers):
            has_key = engine_has_key(info.id)
            name = QTableWidgetItem(info.label)
            name.setData(Qt.ItemDataRole.UserRole, info.id)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, name)
            access = QTableWidgetItem(info.access.label)
            access.setFlags(Qt.ItemFlag.ItemIsEnabled)
            access.setToolTip(info.access_note)
            self.table.setItem(row, 1, access)
            key = QTableWidgetItem("✓" if has_key else "—")
            key.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 2, key)
            for column, scope in ((3, "remote_data"), (4, "costs"), (5, "documents")):
                check = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                if scope == "costs" and not info.access.may_cost_money:
                    check.setText("no aplica")
                    check.setFlags(Qt.ItemFlag.ItemIsEnabled)
                else:
                    check.setFlags(flags)
                    check.setCheckState(
                        Qt.CheckState.Checked
                        if self._c.consents.has(info.id, scope)
                        else Qt.CheckState.Unchecked
                    )
                check.setData(Qt.ItemDataRole.UserRole, scope)
                self.table.setItem(row, column, check)
            if has_key and "chat" in info.capabilities:
                self.remote_provider.addItem(info.label, info.id)
        self.table.resizeColumnsToContents()
        self.table.blockSignals(False)
        self.remote_provider.blockSignals(False)
        self._show_remote_models()

    def _consent_changed(self, item: QTableWidgetItem) -> None:
        scope = item.data(Qt.ItemDataRole.UserRole)
        if scope not in ("remote_data", "costs", "documents"):
            return
        name_item = self.table.item(item.row(), 0)
        if name_item is None:
            return
        provider_id = name_item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            text = {
                "remote_data": "El audio, el texto o las capturas de tus sesiones podrán enviarse a este servicio.",
                "costs": "Su uso puede generar cargos o consumir tu saldo/créditos.",
                "documents": "Los extractos de tus documentos y perfil podrán enviarse a este servicio.",
            }[scope]
            answer = QMessageBox.question(
                self, "Autorizar servicio externo", f"{text}\n\n¿Autorizar?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.table.blockSignals(True)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.table.blockSignals(False)
                return
            self._c.consents.grant(provider_id, scope)
        else:
            self._c.consents.revoke(provider_id, scope)
        self._c.engine.invalidate()
        self._c.settingsChanged.emit()

    def _show_remote_models(self) -> None:
        provider_id = str(self.remote_provider.currentData() or "")
        self.remote_models.setText(
            ", ".join(self._c.settings.remote_models.get(provider_id, []))
        )
        self.remote_list.clear()

    def list_remote_models(self) -> None:
        provider_id = str(self.remote_provider.currentData() or "")
        if not provider_id:
            return
        self.remote_list.clear()
        self.remote_list.addItem("Consultando el catálogo (no consume tokens)…")

        def work() -> list[str]:
            from ...providers.manager import get_client_for

            client = get_client_for(provider_id, timeout=20)
            try:
                return sorted(m.id for m in client.list_models())
            finally:
                client.close()

        def done(models: Any) -> None:
            self.remote_list.clear()
            for model in models:
                self.remote_list.addItem(model)
            self.remote_list.addItem("(doble clic para añadir un modelo)")

        def failed(message: str) -> None:
            self.remote_list.clear()
            self.remote_list.addItem(message)

        run_async(work, on_done=done, on_error=failed)

    def _add_remote_model(self, item: QListWidgetItem) -> None:
        model = item.text()
        if model.startswith("("):
            return
        current = [m.strip() for m in self.remote_models.text().split(",") if m.strip()]
        if model not in current:
            current.append(model)
        self.remote_models.setText(", ".join(current))

    def save_remote_models(self) -> None:
        provider_id = str(self.remote_provider.currentData() or "")
        if not provider_id:
            return
        models = [m.strip() for m in self.remote_models.text().split(",") if m.strip()]
        s = self._c.settings
        if models:
            s.remote_models[provider_id] = models
        else:
            s.remote_models.pop(provider_id, None)
        self._c.save_settings(s)
        self.load_candidates()

    def _fill_usage(self) -> None:
        summary = self._c.ledger.summary()
        if not summary:
            self.usage.setPlainText("Sin uso de servicios externos registrado.")
            return
        lines = []
        for provider_id, totals in summary.items():
            limits = self._c.ledger.rate_limits(provider_id)
            remaining = ", ".join(
                f"{k.replace('x-ratelimit-', '')}={v}"
                for k, v in limits.items()
                if "remaining" in k
            )
            lines.append(
                f"- **{provider_id}**: {int(totals['requests'])} peticiones, "
                f"{int(totals['errors'])} errores, {totals['audio_seconds']:.0f} s de audio, "
                f"{int(totals['output_tokens'])} tokens generados"
                + (f" · restante informado: {remaining}" if remaining else "")
            )
        self.usage.setMarkdown("\n".join(lines))


# =================================================================== Privacidad
class PrivacyPage(QWidget):
    dataCleared = Signal()

    def __init__(self, controller: CopilotController) -> None:
        super().__init__()
        self._c = controller
        layout = QVBoxLayout(self)
        layout.addWidget(title("Privacidad y datos"))
        layout.addWidget(
            hint(
                "Qué ocurre con tus datos:\n"
                "• El audio se procesa en memoria y nunca se guarda en disco.\n"
                "• Las transcripciones y sugerencias de la sesión viven en memoria "
                "hasta que las borras o cierras Extdator; solo se guardan si las exportas.\n"
                "• Las capturas de pantalla se envían al modelo y no quedan en disco "
                "(las temporales del OCR se borran al terminar).\n"
                "• Perfiles y documentos se guardan en tu carpeta de datos local.\n"
                "• A un servicio externo solo se envía contenido si lo autorizaste "
                "en 'Motores y modelos'.\n"
                "• Extdator no oculta sus ventanas de las capturas ni de la "
                "compartición de pantalla."
            )
        )
        for label, action in (
            ("Borrar transcripciones y sugerencias de la sesión", self.clear_session),
            ("Borrar historial de uso de servicios externos", self.clear_usage),
            ("Revocar todos los permisos de servicios externos", self.revoke_all),
            ("Borrar todos los perfiles y documentos", self.clear_knowledge),
        ):
            button = QPushButton(label)
            button.setObjectName("danger")
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addStretch(1)

    def _confirm(self, text: str) -> bool:
        return (
            QMessageBox.question(self, "Confirmar", text)
            == QMessageBox.StandardButton.Yes
        )

    def clear_session(self) -> None:
        if self._confirm("¿Borrar la transcripción y las sugerencias de esta sesión?"):
            self._c.assistant.clear()
            self._c.translator.clear()
            self.dataCleared.emit()

    def clear_usage(self) -> None:
        if self._confirm("¿Borrar el historial de uso?"):
            self._c.ledger.clear()
            self.dataCleared.emit()

    def revoke_all(self) -> None:
        if self._confirm(
            "¿Revocar todos los permisos? Se usarán solo modelos locales."
        ):
            self._c.consents.clear()
            self._c.engine.invalidate()
            self.dataCleared.emit()

    def clear_knowledge(self) -> None:
        if not self._confirm(
            "¿Eliminar todos los perfiles y sus documentos? No se puede deshacer."
        ):
            return
        for profile in self._c.profiles.list():
            self._c.knowledge.delete_profile(profile.id)
            self._c.profiles.delete(profile.id)
        s = self._c.settings
        s.active_profile = ""
        self._c.save_settings(s)
        self.dataCleared.emit()


__all__ = ["AccessTier", "EnginesPage", "HomePage", "PrivacyPage", "ProfilesPage"]
