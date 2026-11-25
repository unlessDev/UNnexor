"""Pruebas de detección de hardware, evaluación de modelos y cliente Ollama."""

from __future__ import annotations

import json
import platform

import httpx
import pytest

from app.hardware import HardwareInfo, detect
from app.model_catalog import catalog_entry, recommended_catalog
from app.model_evaluator import (
    TIER_IMPOSIBLE,
    TIER_PERFECTO,
    TIER_REGULAR,
    assess_model,
    context_warning,
    estimate_requirements,
    gpu_budget,
    params_from_size,
    parse_parameter_count,
)
from app.ollama_client import (
    ModelNotFoundError,
    OllamaClient,
    OllamaResponseError,
)


def ndjson(*objs: dict) -> bytes:
    return ("\n".join(json.dumps(o) for o in objs) + "\n").encode("utf-8")


# --------------------------------------------------------------------- #
# parse_parameter_count / params_from_size
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "parameter_size,name,expected",
    [
        ("8B", "", 8.0),
        ("7.6B", "", 7.6),
        ("0.6B", "", 0.6),
        (None, "qwen3:8b", 8.0),
        (None, "llama3.2:3b", 3.0),  # desde el nombre solo el tag: 3b→3.0
        (None, "mistral-nemo:12b", 12.0),
        (None, "llama3", None),  # sin tag numérico: no se inventa
        ("", "gemma4:26b", 26.0),
        ("4x7B", "", None),  # MoZ estilo no compatible: None
    ],
)
def test_parse_parameter_count(parameter_size, name, expected):
    assert parse_parameter_count(parameter_size, name) == expected


def test_params_from_size():
    assert params_from_size(int(4.4 * 1024**3)) == pytest.approx(8.0, abs=1.0)
    assert params_from_size(0) is None


# --------------------------------------------------------------------- #
# estimate_requirements / gpu_budget
# --------------------------------------------------------------------- #
def test_estimate_requirements_8b_8k():
    metrics = estimate_requirements(int(5.2 * 1024**3), 8.0, 8192)
    assert metrics["kv_cache_gb_est"] == pytest.approx(1.0, abs=0.05)
    assert metrics["runtime_overhead_gb_est"] == 0.5
    assert metrics["total_gb_est"] == pytest.approx(6.7, abs=0.1)


def test_gpu_budget():
    hw = {"gpus": [{"name": "Radeon RX 590", "vram_total_mb": 8192}]}
    usable, known = gpu_budget(hw)
    assert known and usable == pytest.approx(7.6, abs=0.05)
    assert gpu_budget({"gpus": []}) == (None, False)
    # iGPU diminuta se ignora
    igpu = {"gpus": [{"name": "Intel UHD", "vram_total_mb": 128}]}
    assert gpu_budget(igpu) == (None, False)


# --------------------------------------------------------------------- #
# assess_model — escenarios calibrados con el hardware de referencia
# (Ryzen 5 2600X · RX 590 8 GB · 16 GB RAM)
# --------------------------------------------------------------------- #
def _hw_gpu(ram_available_mb: int = 10_000) -> dict:
    return {
        "gpus": [{"name": "AMD Radeon RX 590", "vram_total_mb": 8192}],
        "ram_available_mb": ram_available_mb,
    }


def _hw_cpu(ram_available_mb: int = 10_000) -> dict:
    return {"gpus": [], "ram_available_mb": ram_available_mb}


def test_gpu_fit_with_margin_is_perfecto():
    # 3B en GPU de 8 GB: sobra margen
    a = assess_model("llama3.2:3b", int(2.0 * 1024**3), "3B", 8192, _hw_gpu())
    assert a.tier == TIER_PERFECTO
    assert "GPU" in " ".join(a.reasons)


def test_gpu_tight_is_regular():
    # 8B en GPU de 8 GB: cabe pero ajustado (coincide con ollama ps real)
    a = assess_model("qwen3:8b", int(5.2 * 1024**3), "8B", 8192, _hw_gpu())
    assert a.tier == TIER_REGULAR
    assert a.metrics["target"] == "gpu"


def test_hybrid_mostly_gpu_is_regular():
    a = assess_model("gemma3:12b", int(8.1 * 1024**3), "12B", 8192, _hw_gpu())
    assert a.tier == TIER_REGULAR
    assert a.metrics["gpu_offload_pct"] >= 70


def test_model_beyond_gpu_and_ram_is_imposible():
    a = assess_model("gemma4:26b", int(16.0 * 1024**3), "26B", 8192, _hw_gpu())
    assert a.tier == TIER_IMPOSIBLE
    assert a.metrics["cpu_part_gb"] > a.metrics["ram_available_gb"]


def test_cpu_only_fit_is_perfecto():
    a = assess_model("llama3.1:8b", int(4.9 * 1024**3), "8B", 8192, _hw_cpu())
    assert a.tier == TIER_PERFECTO


def test_cpu_only_huge_is_imposible():
    a = assess_model("gemma4:26b", int(16.0 * 1024**3), "26B", 8192, _hw_cpu())
    assert a.tier == TIER_IMPOSIBLE


def test_no_disk_space_for_pull_is_imposible():
    a = assess_model(
        "qwen3:8b",
        int(5.2 * 1024**3),
        "8B",
        8192,
        _hw_gpu(),
        installed=False,
        disk_free_gb=1.0,
    )
    assert a.tier == TIER_IMPOSIBLE
    assert any("espacio" in r.lower() for r in a.reasons)


def test_unknown_data_falls_back_to_regular():
    a = assess_model("modelo-misterioso", None, None, None, _hw_gpu())
    assert a.tier == TIER_REGULAR
    assert any("sin datos" in r.lower() for r in a.reasons)


def test_assessment_dict_shape():
    a = assess_model("qwen3:8b", int(5.2 * 1024**3), "8B", 8192, _hw_gpu())
    data = a.to_dict()
    for key in (
        "model",
        "tier",
        "label",
        "emoji",
        "color",
        "explanation",
        "reasons",
        "metrics",
    ):
        assert key in data


def test_context_warning():
    assert context_warning(32_768) is not None
    assert context_warning(4096) is None


# --------------------------------------------------------------------- #
# Catálogo recomendado
# --------------------------------------------------------------------- #
def test_catalog_entries_have_source_and_vision_flag():
    catalog = recommended_catalog()
    assert len(catalog) >= 10
    vision_models = [m for m in catalog if m["vision"]]
    assert vision_models, "debe incluir modelos con visión"
    for entry in catalog:
        assert entry["source"].startswith("https://ollama.com/library")
        assert entry["size_bytes"] > 0
    assert catalog_entry("qwen3:8b")["params_b"] == 8.0
    assert catalog_entry("inexistente:xx") is None


# --------------------------------------------------------------------- #
# Detección real (Windows: registro + psutil)
# --------------------------------------------------------------------- #
@pytest.mark.skipif(platform.system() != "Windows", reason="registro de Windows")
def test_detect_real_machine():
    info = detect()
    assert isinstance(info, HardwareInfo)
    assert info.cpu_threads >= 2
    assert info.ram_total_mb > 1_000
    assert info.ram_available_mb > 0
    assert info.detected_at
    assert info.detection_ms < 5_000
    data = info.to_dict()
    assert "gpus" in data and "disk_free_gb" in data


# --------------------------------------------------------------------- #
# Cliente Ollama: show / ps / pull_stream
# --------------------------------------------------------------------- #
def test_show_model_and_404():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/show"
        body = json.loads(request.content)
        if body["model"] == "qwen3:8b":
            return httpx.Response(
                200,
                json={
                    "family": "qwen3",
                    "parameter_size": "8B",
                    "quantization_level": "Q4_K_M",
                    "capabilities": ["completion", "vision"],
                    "model_info": {"qwen3.context_length": 40960},
                },
            )
        return httpx.Response(404, json={"error": "model not found"})

    client = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler))
    info = client.show("qwen3:8b")
    assert info["capabilities"] == ["completion", "vision"]
    with pytest.raises(ModelNotFoundError):
        client.show("noexiste")


def test_running_models_reports_vram():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3:8b",
                        "size": 6_700_000_000,
                        "size_vram": 6_700_000_000,
                        "expiry": "5m",
                    },
                ]
            },
        )

    client = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler))
    running = client.running_models()
    assert running[0]["size_vram"] > 0


def test_pull_stream_progress_and_error():
    lines_ok = [
        {"status": "pulling manifest"},
        {"status": "downloading", "total": 1000, "completed": 400},
        {"status": "downloading", "total": 1000, "completed": 1000},
        {"status": "success"},
    ]
    seen: list[tuple] = []

    def handler_ok(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "qwen3:8b"
        return httpx.Response(200, content=ndjson(*lines_ok))

    client = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler_ok))
    final = client.pull_stream(
        "qwen3:8b", on_progress=lambda s, t, c: seen.append((s, t, c))
    )
    assert final == "success"
    assert seen[-1] == ("success", None, None)
    assert seen[1] == ("downloading", 1000, 400)

    def handler_err(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson({"error": "model not found"}))

    client_err = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler_err))
    with pytest.raises(OllamaResponseError, match="model not found"):
        client_err.pull_stream("noexiste")


def test_installed_models_full():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3:8b",
                        "size": 5_200_000_000,
                        "details": {
                            "parameter_size": "8B",
                            "quantization_level": "Q4_K_M",
                        },
                    },
                ]
            },
        )

    client = OllamaClient(make_cfg(), transport=httpx.MockTransport(handler))
    models = client.installed_models_full()
    assert models[0]["details"]["parameter_size"] == "8B"


def make_cfg():
    from app.config import Config

    cfg = Config()
    cfg.token = ""
    return cfg
