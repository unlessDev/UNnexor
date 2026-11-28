"""Catálogo en línea de Ollama, versión y recomendaciones por perfil."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.compat.advisor import CONTEXT, recommend_all
from app.model_evaluator import TIER_ALTO, TIER_IMPOSIBLE
from app.ollama_library import (
    LibraryCatalog,
    LibrarySnapshot,
    catalog_entries,
    parse_library,
    size_to_params,
    version_status,
)

CARD = """
<a href="/library/{name}" class="group w-full space-y-5">
<p class="max-w-lg break-words text-neutral-800 text-md">{desc}</p>
{caps}{sizes}
<span >1.2M</span>
<span class="hidden sm:flex">&nbsp;Pulls</span>
</a>
"""


def page(*models: tuple[str, list[str], list[str]]) -> str:
    cards = []
    for name, caps, sizes in models:
        cards.append(
            CARD.format(
                name=name,
                desc=f"Modelo {name}",
                caps="".join(
                    f'<span  class="inline-flex items-center rounded-md bg-indigo-50 px-2">{c}</span>'
                    for c in caps
                ),
                sizes="".join(
                    f'<span  class="inline-flex items-center rounded-md bg-[#ddf4ff] px-2">{s}</span>'
                    for s in sizes
                ),
            )
        )
    return "".join(cards)


BASE = [(f"m{i}", ["tools"], ["4b"]) for i in range(12)]


def test_parse_library_and_entries():
    html = page(
        ("qwen3", ["tools", "thinking"], ["0.6b", "8b", "235b"]),
        ("gemma3", ["vision"], ["4b"]),
        ("nomic-embed-text", ["embedding"], []),
        ("bigcloud", ["cloud"], []),
    )
    models = parse_library(html)
    assert [m.name for m in models] == [
        "qwen3",
        "gemma3",
        "nomic-embed-text",
        "bigcloud",
    ]
    assert models[0].capabilities == ["tools", "thinking"] and models[0].pulls == "1.2M"
    entries = catalog_entries(LibrarySnapshot(models=models, fetched_at="2026-11-27"))
    names = [e["name"] for e in entries]
    assert names == ["qwen3:0.6b", "qwen3:8b", "gemma3:4b"]  # sin 235b, embed ni cloud
    assert all(e["estimated"] for e in entries)
    assert next(e for e in entries if e["name"] == "gemma3:4b")["vision"]
    assert size_to_params("e4b") == 4.0 and size_to_params("raro") is None


def test_version_status():
    assert version_status("0.40.17", "0.40.18")[0] == "update"
    assert version_status("0.40.18", "v0.40.18")[0] == "ok"
    assert version_status("", "0.40.18")[0] == "unknown"


def test_refresh_detects_new_models_and_keeps_cache_offline(tmp_path):
    pages = [page(*BASE), page(*BASE, ("nuevo", ["tools"], ["8b"]))]
    online = {"up": True}

    def get(url, **_kw):
        request = httpx.Request("GET", url)
        if not online["up"]:
            raise httpx.ConnectError("sin red", request=request)
        if "github" in url:
            return httpx.Response(200, json={"tag_name": "v0.41.0"}, request=request)
        return httpx.Response(200, text=pages.pop(0), request=request)

    clock = lambda: datetime(2026, 11, 27, tzinfo=timezone.utc)  # noqa: E731
    catalog = LibraryCatalog(tmp_path / "lib.json", http_get=get, clock=clock)
    first = catalog.refresh()
    assert first.status == "online" and first.new_names == []  # 1.ª consulta
    assert first.latest_ollama == "0.41.0"
    second = catalog.refresh()
    assert second.new_names == ["nuevo"]
    online["up"] = False
    offline = catalog.refresh()
    assert offline.status == "cache" and len(offline.models) == 13 and offline.error


# ------------------------------------------------------------ recomendaciones
HW_8GB = {
    "ram_total_mb": 16384,
    "ram_available_mb": 9000,
    "gpus": [{"name": "GPU", "vendor": "AMD", "vram_total_mb": 8192}],
    "backends": {"vulkan": True},
    "disk_free_gb": 200.0,
}
HW_CPU = {
    "ram_total_mb": 8192,
    "ram_available_mb": 4000,
    "gpus": [],
    "backends": {},
    "disk_free_gb": 50.0,
}
INSTALLED = [
    {
        "name": "qwen3:8b",
        "size_bytes": 5_200_000_000,
        "parameter_size": "8.2B",
        "capabilities": ["completion", "thinking"],
    },
    {
        "name": "qwen2.5vl:7b",
        "size_bytes": 6_000_000_000,
        "parameter_size": "8.3B",
        "capabilities": ["completion", "vision"],
    },
]
CATALOG = [
    {"name": "qwen3:4b", "params_b": 4.0, "size_bytes": 2_600_000_000},
    {"name": "llama3.1:70b", "params_b": 70.0, "size_bytes": 43_000_000_000},
    {"name": "gemma3:4b", "params_b": 4.3, "size_bytes": 3_300_000_000, "vision": True},
]


@pytest.mark.parametrize("profile", ["low", "medium", "high"])
@pytest.mark.parametrize("hw", [HW_8GB, HW_CPU])
def test_never_recommends_demanding_models(profile, hw):
    result = recommend_all(hw, profile=profile, installed=INSTALLED, catalog=CATALOG)
    for rec in result["recommendations"]:
        assert rec.tier not in (TIER_ALTO, TIER_IMPOSIBLE) or not rec.available
        assert "70b" not in rec.choice


def test_profiles_scale_and_features_are_independent():
    low = recommend_all(HW_8GB, profile="low", installed=INSTALLED, catalog=CATALOG)
    high = recommend_all(HW_8GB, profile="high", installed=INSTALLED, catalog=CATALOG)
    by = lambda r: {x.feature: x for x in r["recommendations"]}  # noqa: E731
    lo, hi = by(low), by(high)
    assert set(lo) == {
        "chat",
        "conversation",
        "translation",
        "vision",
        "stt",
        "ocr",
        "documents",
    }
    assert lo["chat"].choice == "qwen3:4b" and hi["chat"].choice == "qwen3:8b"
    assert lo["chat"].download == "ollama pull qwen3:4b" and hi["chat"].download == ""
    assert lo["chat"].params["num_ctx"] == CONTEXT["low"]
    # Funciones distintas, parámetros distintos.
    assert (
        lo["translation"].params["temperature"]
        < lo["conversation"].params["temperature"]
    )
    assert lo["translation"].params["think"] is False
    assert "vision" in lo and lo["vision"].choice == "gemma3:4b"
    assert hi["vision"].params["num_ctx"] >= 8192


def test_acceleration_report_distinguishes_states():
    hw = dict(HW_8GB, ollama_gpu_active=False)
    rows = recommend_all(hw, profile="medium", installed=[], catalog=CATALOG)[
        "acceleration"
    ]
    assert rows[0]["detected"] == "sí" and "Vulkan" in rows[0]["compatible"]
    assert rows[0]["used"].startswith("no")


def test_dialog_plan_applies_only_installed_and_restores(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.copilot.settings import CopilotSettings
    from app.desktop import recommend_dialog
    from tests.conftest import make_config

    monkeypatch.setattr(recommend_dialog.paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    cfg = make_config()
    cfg.ollama_model = "viejo:7b"
    applied: list[dict] = []
    settings = CopilotSettings()
    saved: list[CopilotSettings] = []
    dialog = recommend_dialog.RecommendationDialog(
        cfg,
        ollama=None,  # type: ignore[arg-type]
        apply_main=applied.append,
        copilot_settings=lambda: settings,
        save_copilot=saved.append,
    )
    result = recommend_all(HW_8GB, profile="high", installed=INSTALLED, catalog=CATALOG)
    result["hardware"] = HW_8GB
    result["installed_names"] = [m["name"] for m in INSTALLED]
    dialog._on_done(result)
    assert len(dialog._rec_cards) == 7
    dialog.apply()
    assert applied[-1]["ollama_model"] == "qwen3:8b"
    assert settings.pinned["answer"] == "ollama:qwen3:8b"
    assert settings.task_temperature["translation"] == 0.2
    assert (tmp_path / "config_backup.json").is_file()
    dialog.restore()
    assert applied[-1]["ollama_model"] == "viejo:7b"
    assert settings.pinned == {} and settings.task_temperature == {}
    dialog.deleteLater()
    qapp.sendPostedEvents(None, 52)
