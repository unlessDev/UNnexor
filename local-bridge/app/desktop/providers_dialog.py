"""Diálogo de administración de proveedores de API (FASE 4).

Catálogo visual por categorías + conexión guiada:

  1. El usuario elige un proveedor de la lista.
  2. Introduce su clave (oculta; botón para mostrar).
  3. "Probar y conectar" valida la clave consultando el catálogo de
     modelos (NO consume tokens) y la guarda en el Credential Manager.
  4. Confirmación inequívoca: "Conexión establecida con X. N modelos
     disponibles para tu cuenta."
  5. "Elegir modelo" lista los modelos reales de la cuenta y activa uno.

Las operaciones de red corren en QThreads (la GUI nunca se bloquea).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..providers import manager
from ..providers.base import ProviderError
from ..providers.presets import PRESETS
from .styles import ACCENT, BORDER, GREEN, MUTED, PANEL, RED, TEXT

# Agrupación por acceso documentado (ver presets.py para las fuentes).
CATEGORIES = [
    ("Gratuitos con límites documentados", ("groq", "mistral", "gemini", "cerebras")),
    ("Créditos según plan", ("hf_router", "nvidia_nim")),
    ("Gateways con modelos free y de pago", ("openrouter",)),
    ("De pago", ("openai", "together", "xai", "moonshot", "zai")),
]


# --------------------------------------------------------------------- #
# Workers de red
# --------------------------------------------------------------------- #
class ConnectWorker(QThread):
    done = Signal(str, dict)  # (provider_id, resultado)
    failed = Signal(str, str)  # (provider_id, mensaje)

    def __init__(
        self,
        provider_id: str,
        key: str,
        base_url: str = "",
        label: str = "",
        parent: Optional[QThread] = None,
    ) -> None:
        super().__init__(parent)
        self._id = provider_id
        self._key = key
        self._base_url = base_url
        self._label = label

    def run(self) -> None:
        try:
            result = manager.connect(
                self._id,
                self._key,
                base_url=self._base_url or None,
                label=self._label,
            )
            self.done.emit(self._id, result)
        except ProviderError as exc:
            self.failed.emit(self._id, exc.message)

    def label_for(self) -> str:
        return self._label


class ModelListWorker(QThread):
    done = Signal(str, list)  # (provider_id, [ids])
    failed = Signal(str, str)

    def __init__(self, provider_id: str, parent: Optional[QThread] = None) -> None:
        super().__init__(parent)
        self._id = provider_id

    def run(self) -> None:
        try:
            client = manager.get_client_for(self._id, timeout=20.0)
        except ProviderError as exc:
            self.failed.emit(self._id, exc.message)
            return
        try:
            models = client.list_models()
        except ProviderError as exc:
            self.failed.emit(self._id, exc.message)
            return
        finally:
            client.close()
        self.done.emit(self._id, [m.id for m in models])


# --------------------------------------------------------------------- #
# Tarjeta de proveedor
# --------------------------------------------------------------------- #
class ProviderCard(QFrame):
    clicked = Signal(str)

    def __init__(
        self,
        preset: dict,
        connected: bool,
        model_count: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.preset = preset
        self.provider_id = preset["id"]
        self.setObjectName("providerCard")
        border = GREEN if connected else BORDER
        self.setStyleSheet(
            f"#providerCard {{ background: {PANEL}; border: 1px solid "
            f"{border}; border-radius: 8px; }}\n"
            f"#providerCard:hover {{ border-color: {ACCENT}; }}\n"
            "QLabel { background: transparent; border: none; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        head = QHBoxLayout()
        icon = QLabel(preset["label"][:1].upper())
        icon.setStyleSheet(f"color: {ACCENT}; font-size: 16px; font-weight: 700;")
        name = QLabel(preset["label"])
        name.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        head.addWidget(icon)
        head.addWidget(name)
        head.addStretch(1)
        status = QLabel(
            f"● conectado · {model_count} modelos" if connected else "○ sin conectar"
        )
        status.setStyleSheet(
            f"color: {GREEN if connected else MUTED}; font-size: 10px;"
        )
        head.addWidget(status)
        layout.addLayout(head)

        desc = QLabel(preset["description"])
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(desc)

        kind = QLabel(f"Acceso: {preset['kind']}")
        kind.setWordWrap(True)
        kind.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(kind)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.provider_id)
        super().mousePressEvent(event)


# --------------------------------------------------------------------- #
# Diálogo principal
# --------------------------------------------------------------------- #
class ProvidersDialog(QDialog):
    """Catálogo de proveedores + flujo de conexión + selección de modelo."""

    providerChosen = Signal(str, str)  # (provider_id, modelo)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Proveedores de IA — Extdator")
        self.setMinimumSize(600, 620)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}\n"
            "QLabel { background: transparent; }\n"
            f"QLineEdit {{ background: #0e1016; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}\n"
            f"QPushButton {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px; }}\n"
            f"QPushButton:hover {{ border-color: {ACCENT}; }}\n"
            f"QComboBox {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}\n"
            f"QScrollBar:vertical {{ width: 10px; }}\n"
            "QScrollBar::handle:vertical { background: #2a3140; border-radius: 5px; }"
        )
        self._cards: dict = {}
        self._workers: list = []
        self._selected_id: str = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header = QLabel("Proveedores de IA en la nube")
        header.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700;")
        privacy = QLabel(
            "Tus claves se guardan SOLO en el Credential Manager de Windows "
            "(keyring). Nunca en archivos, logs ni en la extensión. "
            "«Probar y conectar» consulta el catálogo de modelos: no "
            "consume tokens de chat."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        layout.addWidget(header)
        layout.addWidget(privacy)

        # --- Catálogo --------------------------------------------------- #
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(6)
        for category, ids in CATEGORIES:
            label = QLabel(category)
            label.setStyleSheet(
                f"color: {ACCENT}; font-size: 11px; font-weight: 700;"
                "letter-spacing: 1px; padding-top: 4px;"
            )
            vbox.addWidget(label)
            for preset in PRESETS:
                if preset["id"] not in ids:
                    continue
                state = manager.provider_state(preset["id"])
                card = ProviderCard(
                    preset,
                    state.get("connected", False),
                    state.get("model_count", 0),
                )
                card.clicked.connect(self._select)
                self._cards[preset["id"]] = card
                vbox.addWidget(card)
        vbox.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # --- Panel de detalle / conexión -------------------------------- #
        detail = QFrame()
        detail.setStyleSheet(
            f"QFrame {{ background: {PANEL}; border: 1px solid {BORDER};"
            " border-radius: 8px; } QLabel { border: none; }"
        )
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(10, 8, 10, 8)
        self._detail_title = QLabel("Selecciona un proveedor de la lista")
        self._detail_title.setStyleSheet(f"color: {TEXT}; font-weight: 700;")
        self._detail_desc = QLabel("")
        self._detail_desc.setWordWrap(True)
        self._detail_desc.setStyleSheet(f"color: {MUTED}; font-size: 10px;")

        key_row = QHBoxLayout()
        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText("Clave API del proveedor…")
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._eye = QToolButton()
        self._eye.setText("👁")
        self._eye.setToolTip("Mostrar / ocultar la clave")
        self._eye.setCheckable(True)
        self._eye.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._key_input, 1)
        key_row.addWidget(self._eye)

        self._custom_url = QLineEdit()
        self._custom_url.setPlaceholderText(
            "URL base OpenAI-compatible (solo proveedor personalizado)…"
        )
        self._custom_url.setVisible(False)

        action_row = QHBoxLayout()
        self._connect_btn = QPushButton("Probar y conectar")
        self._connect_btn.clicked.connect(self._connect_selected)
        self._choose_btn = QPushButton("Elegir modelo…")
        self._choose_btn.clicked.connect(self._choose_model)
        self._disconnect_btn = QPushButton("Desconectar")
        self._disconnect_btn.clicked.connect(self._disconnect_selected)
        action_row.addWidget(self._connect_btn)
        action_row.addWidget(self._choose_btn)
        action_row.addWidget(self._disconnect_btn)
        action_row.addStretch(1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")

        detail_layout.addWidget(self._detail_title)
        detail_layout.addWidget(self._detail_desc)
        detail_layout.addLayout(key_row)
        detail_layout.addWidget(self._custom_url)
        detail_layout.addLayout(action_row)
        detail_layout.addWidget(self._status)
        layout.addWidget(detail)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self._update_action_buttons()

    # ------------------------------------------------------------------ #
    def _select(self, provider_id: str) -> None:
        self._selected_id = provider_id
        if provider_id == "custom":
            self._detail_title.setText("Proveedor personalizado (OpenAI-compatible)")
            self._detail_desc.setText(
                "Conecta cualquier servicio compatible con el formato OpenAI "
                "indicando su URL base."
            )
            self._custom_url.setVisible(True)
            state = manager.provider_state("custom")
            self._custom_url.setText(state.get("base_url", "") or "")
        else:
            preset = next(p for p in PRESETS if p["id"] == provider_id)
            self._detail_title.setText(preset["label"])
            self._detail_desc.setText(
                f"{preset['description']}\nAcceso: {preset['kind']} · "
                f"Formato de clave: {preset['key_format']}\n"
                f"Documentación: {preset['docs']}"
            )
            self._custom_url.setVisible(False)
        self._update_action_buttons()

    def _toggle_key_visibility(self, show: bool) -> None:
        self._key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
        )

    def _update_action_buttons(self) -> None:
        connected = (
            bool(self._selected_id) and manager.get_key(self._selected_id) is not None
        )
        self._connect_btn.setEnabled(bool(self._selected_id))
        self._connect_btn.setText(
            "Probar y conectar" if not connected else "Reemplazar clave"
        )
        self._choose_btn.setEnabled(connected)
        self._disconnect_btn.setEnabled(connected)

    # ------------------------------------------------------------------ #
    def _connect_selected(self) -> None:
        if not self._selected_id:
            return
        key = self._key_input.text().strip()
        if not key:
            self._set_status(
                "Introduce la clave API del proveedor (se guarda en el "
                "keyring del sistema).",
                error=False,
            )
            return
        base_url = (
            self._custom_url.text().strip() if self._selected_id == "custom" else ""
        )
        worker = ConnectWorker(self._selected_id, key, base_url=base_url)
        worker.done.connect(self._on_connected)
        worker.failed.connect(self._on_connect_failed)
        self._workers.append(worker)
        self._connect_btn.setEnabled(False)
        self._set_status("Probando la conexión con el proveedor…")
        worker.start()

    def _on_connected(self, provider_id: str, result: dict) -> None:
        self._key_input.clear()
        self._refresh_cards()
        self._update_action_buttons()
        count = result.get("model_count", 0)
        sample = ", ".join(result.get("sample", [])[:3])
        message = (
            f"✓ Conexión establecida correctamente. {count} modelos "
            f"disponibles para tu cuenta." + (f" Ejemplos: {sample}…" if sample else "")
        )
        self._set_status(message, error=False)
        self._status.setStyleSheet(f"color: {GREEN}; font-size: 10px;")

    def _on_connect_failed(self, provider_id: str, message: str) -> None:
        self._update_action_buttons()
        self._set_status(f"✗ {message}", error=True)

    # ------------------------------------------------------------------ #
    def _choose_model(self) -> None:
        if not self._selected_id:
            return
        worker = ModelListWorker(self._selected_id)
        worker.done.connect(self._on_models_ready)
        worker.failed.connect(self._on_models_failed)
        self._workers.append(worker)
        self._choose_btn.setEnabled(False)
        self._set_status("Consultando los modelos disponibles…")
        worker.start()

    def _on_models_ready(self, provider_id: str, model_ids: List[str]) -> None:
        self._choose_btn.setEnabled(True)
        if not model_ids:
            self._set_status(
                "El proveedor no expuso ningún modelo vía /models; escribe "
                "el identificador manualmente.",
                error=False,
            )
            model_ids = [""]
        dialog = _ModelPickDialog(model_ids, provider_id, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            model = dialog.selected()
            if model:
                self.providerChosen.emit(provider_id, model)
                self.accept()

    def _on_models_failed(self, provider_id: str, message: str) -> None:
        self._choose_btn.setEnabled(True)
        self._set_status(f"✗ {message}", error=True)

    # ------------------------------------------------------------------ #
    def _disconnect_selected(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not self._selected_id:
            return
        answer = QMessageBox.question(
            self,
            "Desconectar proveedor",
            "Se borrará la clave guardada en el Credential Manager y dejarás "
            "de poder usar este proveedor.\n\n¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        manager.disconnect(self._selected_id)
        self._refresh_cards()
        self._update_action_buttons()
        self._set_status("Proveedor desconectado y clave eliminada.", error=False)

    def _refresh_cards(self) -> None:
        for provider_id, card in self._cards.items():
            state = manager.provider_state(provider_id)
            connected = state.get("connected", False)
            # Recrear la tarjeta con el estado actualizado.
            new_card = ProviderCard(card.preset, connected, state.get("model_count", 0))
            new_card.clicked.connect(self._select)
            parent_layout = card.parentWidget().layout()
            index = parent_layout.indexOf(card)
            parent_layout.insertWidget(index, new_card)
            card.deleteLater()
            self._cards[provider_id] = new_card

    def _set_status(self, message: str, error: bool = False) -> None:
        self._status.setText(message)
        self._status.setStyleSheet(
            f"color: {RED if error else MUTED}; font-size: 10px;"
        )

    def closeEvent(self, event) -> None:
        for worker in self._workers:
            if worker.isRunning():
                worker.wait(1500)
        super().closeEvent(event)


# --------------------------------------------------------------------- #
class _ModelPickDialog(QDialog):
    """Elección de modelo de un proveedor conectado."""

    def __init__(
        self, model_ids: List[str], provider_id: str, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Modelos de {provider_id}")
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"QDialog {{ background: #12141a; color: {TEXT}; }}\n"
            "QLabel { background: transparent; }"
            f"QComboBox {{ background: #20242e; color: {TEXT};"
            f" border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}"
        )
        self._provider_id = provider_id
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.addItems(model_ids)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Elige el modelo que se usará para responder (catálogo real de "
                "tu cuenta):"
            )
        )
        layout.addWidget(self._combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> str:
        return self._combo.currentText().strip()
