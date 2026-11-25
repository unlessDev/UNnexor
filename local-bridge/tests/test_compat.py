"""Compatibilidad: backends, clases públicas, carga combinada y mediciones."""

from __future__ import annotations

import json

import httpx
import pytest

from app.compat.benchmark import benchmark_llm
from app.compat.workload import assess_workload, ocr_item, ollama_item, stt_item
from app.copilot.settings import CopilotSettings
from app.model_evaluator import (
    TIER_ALTO,
    TIER_IMPOSIBLE,
    TIER_META,
    TIER_PERFECTO,
    TIER_SIN_EVALUAR,
    gpu_backend_available,
    gpu_budget,
)
from app.ollama_client import OllamaClient
from tests.conftest import make_config

GB = 1024**3


def hw(vendor="AMD", vram=8192, free=12000, total=16000, backends=None):
    data = {
        "ram_total_mb": total,
        "ram_available_mb": free,
        "gpus": (
            [{"name": "GPU", "vendor": vendor, "vram_total_mb": vram}] if vram else []
        ),
    }
    if backends is not None:
        data["backends"] = backends
    return data


def test_public_labels_are_the_five_classes():
    labels = {meta[1] for meta in TIER_META.values()}
    assert labels == {
        "ÓPTIMO",
        "COMPATIBLE",
        "EXIGENTE",
        "NO COMPATIBLE",
        "NO EVALUADO",
    }


def test_gpu_without_backend_is_not_counted():
    assert gpu_budget(hw(backends={"vulkan": True}))[0] == pytest.approx(7.6)
    assert gpu_budget(hw(backends={"vulkan": False, "hip_runtime": False}))[0] is None
    assert gpu_budget(hw())[0] == pytest.approx(7.6)  # sin datos: comportamiento previo
    assert gpu_backend_available("NVIDIA", {"cuda_driver": True})
    assert not gpu_backend_available("NVIDIA", {"cuda_driver": False, "vulkan": False})
    assert gpu_backend_available("Intel", {"hip_runtime": False, "vulkan": True})


def test_workload_tiers_match_observed_behaviour():
    llm = ollama_item("qwen3:8b", int(5.2 * GB), "8.2B")
    vision = ollama_item("qwen2.5vl:7b", int(6.0 * GB), "8.3B", "vision")
    machine = hw(backends={"vulkan": True})
    assert assess_workload(machine, [stt_item("base", 500), llm]).tier == TIER_PERFECTO
    both = assess_workload(machine, [stt_item("base", 500), llm, vision])
    assert both.tier == TIER_ALTO
    assert any("alternará" in r for r in both.reasons)
    tight = assess_workload(
        hw(backends={"vulkan": True}, free=3600),
        [stt_item("small", 1100), llm, vision, ocr_item("pix2text")],
    )
    assert tight.tier == TIER_ALTO and any("Pix2Text" in r for r in tight.reasons)


def test_workload_impossible_only_beyond_total_ram_and_unknown_sizes():
    huge = ollama_item("llama:70b", int(42 * GB), "70B")
    cpu_only = hw(vram=0, total=16000, free=12000)
    assert assess_workload(cpu_only, [huge]).tier == TIER_IMPOSIBLE
    unknown = ollama_item("misterio", None, None)
    result = assess_workload(cpu_only, [unknown])
    assert result.tier == TIER_SIN_EVALUAR and "misterio" in result.reasons[0]


def test_measured_stt_values_feed_recommender():
    settings = CopilotSettings(
        benchmarks={"stt:small": {"call_s": 2.1}, "llm:qwen3:8b": {"tokens_per_s": 30}}
    )
    assert settings.measured_stt_call_s() == {"small": 2.1}


def test_benchmark_llm_reports_load_first_token_and_speed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "m", "size_vram": 512 * 2**20}]}
            )
        lines = [
            {"message": {"content": "Hola "}},
            {"message": {"content": "mundo"}},
            {"done": True, "eval_count": 20, "eval_duration": 1_000_000_000},
        ]
        return httpx.Response(
            200, content="\n".join(json.dumps(x) for x in lines).encode()
        )

    client = OllamaClient(make_config(), transport=httpx.MockTransport(handler))
    result = benchmark_llm(client, "m")
    assert result["tokens_per_s"] == 20.0
    assert result["first_token_s"] is not None and result["vram_mb"] == 512
    assert result["chars"] == len("Hola mundo")
