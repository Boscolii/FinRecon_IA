import json

import pytest

from conciliacao import server
from conciliacao.generator import generate


def configure_server(tmp_path, monkeypatch):
    data = tmp_path / "data"
    generate(data / "banco.db", data / "bandeira.csv", data / "gabarito.json", n=40, per_type=1, benign=1, seed=3)
    monkeypatch.setattr(server, "DB_PATH", data / "banco.db")
    monkeypatch.setattr(server, "DATA_DIR", data)
    monkeypatch.setattr(server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(server, "LOG_PATH", tmp_path / "logs" / "audit.jsonl")
    return data


def test_server_registers_the_expected_mcp_tools():
    assert set(server.mcp._tool_manager._tools) == {
        "listar_schema", "consultar_transacoes", "ler_csv", "conciliar", "listar_divergencias", "detalhar_divergencia", "gerar_relatorio"
    }
    detail_schema = server.mcp._tool_manager._tools["detalhar_divergencia"].parameters
    assert detail_schema["required"] == ["execucao_id", "finding_id"]
    assert server.mcp._tool_manager._tools["conciliar"].parameters["properties"]["tolerancia_centavos"]["type"] == "integer"


def test_safe_csv_rejects_path_escape_and_oversized_file(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(server, "DATA_DIR", data)
    outside = tmp_path / "outside.csv"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="permitido"):
        server._safe_csv("../outside.csv")
    oversized = data / "large.csv"
    oversized.write_bytes(b"x" * (server.MAX_CSV_BYTES + 1))
    with pytest.raises(ValueError, match="tamanho"):
        server._safe_csv("large.csv")


def test_server_rejects_invalid_query_limits_and_inverted_dates(tmp_path, monkeypatch):
    configure_server(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        server.consultar_transacoes(data_inicio="2026-10-20", data_fim="2026-10-19")
    with pytest.raises(ValueError):
        server.ler_csv(arquivo="bandeira.csv", limite=0)


def test_run_id_is_deterministic_and_detail_uses_finding_id(tmp_path, monkeypatch):
    configure_server(tmp_path, monkeypatch)
    first = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")
    second = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")
    assert first["execucao_id"] == second["execucao_id"]
    assert len(first["execucao_id"]) == 64
    finding = first["divergences"][0]["finding_id"]
    assert server.detalhar_divergencia(execucao_id=first["execucao_id"], finding_id=finding)["finding_id"] == finding
    run = json.loads((server.RUNS_DIR / f"{first['execucao_id']}.json").read_text(encoding="utf-8"))
    assert run["metadata"]["rules_version"] == server.RULES_VERSION


def test_paginated_findings_return_every_item_and_reject_invalid_limits(tmp_path, monkeypatch):
    configure_server(tmp_path, monkeypatch)
    run_id = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")["execucao_id"]
    first = server.listar_divergencias(execucao_id=run_id, inicio=0, limite=2)
    second = server.listar_divergencias(execucao_id=run_id, inicio=first["proximo_inicio"], limite=2)
    third = server.listar_divergencias(execucao_id=run_id, inicio=second["proximo_inicio"], limite=2)
    all_items = first["divergencias"] + second["divergencias"] + third["divergencias"]
    assert first["total"] == 6 and len(all_items) == 6
    assert third["proximo_inicio"] is None
    assert len({item["finding_id"] for item in all_items}) == 6
    with pytest.raises(ValueError):
        server.listar_divergencias(execucao_id=run_id, inicio=-1)
    with pytest.raises(ValueError):
        server.listar_divergencias(execucao_id=run_id, limite=101)
