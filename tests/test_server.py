import json
import hashlib
import sqlite3

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


def test_consultar_transacoes_uses_actual_instants_with_offsets(tmp_path, monkeypatch):
    data = configure_server(tmp_path, monkeypatch)
    with sqlite3.connect(data / "banco.db") as conn:
        conn.execute("DELETE FROM transactions")
        conn.executemany("INSERT INTO transactions (nsu, merchant, amount_cents, occurred_at, status, card_last4) VALUES (?, 'LOJA', ?, ?, 'approved', '1234')", [
            ("incluida", 100, "2026-10-19T01:00:00-03:00"),
            ("fora", 200, "2026-10-20T03:00:00+00:00"),
        ])
    result = server.consultar_transacoes(data_inicio="2026-10-19", data_fim="2026-10-19")
    assert result["total"] == 1 and result["soma_centavos"] == 100
    assert result["transacoes"][0]["nsu"] == "incluida"


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


def test_identical_missing_records_can_each_be_detailed(tmp_path, monkeypatch):
    data = configure_server(tmp_path, monkeypatch)
    with sqlite3.connect(data / "banco.db") as conn:
        conn.execute("DELETE FROM transactions")
        for _ in range(2):
            conn.execute("INSERT INTO transactions (nsu, merchant, amount_cents, occurred_at, status, card_last4) VALUES ('', 'LOJA', 100, '2026-10-19T12:00:00+00:00', 'approved', '1234')")
    (data / "bandeira.csv").write_text("nsu;estabelecimento;valor;data_hora;situacao;final_cartao\n", encoding="utf-8")
    run = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")
    findings = run["divergences"]
    assert len(findings) == 2 and findings[0]["finding_id"] != findings[1]["finding_id"]
    for finding in findings:
        assert server.detalhar_divergencia(execucao_id=run["execucao_id"], finding_id=finding["finding_id"]) == finding


def test_run_hash_and_results_use_same_csv_snapshot(tmp_path, monkeypatch):
    data = configure_server(tmp_path, monkeypatch)
    csv_path = data / "bandeira.csv"
    original = csv_path.read_bytes()
    load = server.engine.load_csv
    def alter_file_after_snapshot(content, *args):
        assert isinstance(content, bytes)
        csv_path.write_text("arquivo alterado", encoding="utf-8")
        return load(content, *args)
    monkeypatch.setattr(server.engine, "load_csv", alter_file_after_snapshot)
    result = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")
    run = json.loads((server.RUNS_DIR / f"{result['execucao_id']}.json").read_text(encoding="utf-8"))
    assert run["metadata"]["csv_sha256"] == hashlib.sha256(original).hexdigest()
    assert run["summary"]["csv_total"] > 0


def test_day_scope_keeps_adjacent_day_candidate_and_excludes_unrelated_rows(tmp_path, monkeypatch):
    data = configure_server(tmp_path, monkeypatch)
    with sqlite3.connect(data / "banco.db") as conn:
        conn.execute("DELETE FROM transactions")
        conn.executemany("INSERT INTO transactions (nsu, merchant, amount_cents, occurred_at, status, card_last4) VALUES (?, 'LOJA', 100, ?, 'approved', '1234')", [
            ("par", "2026-10-19T03:05:00+00:00"),
            ("outro", "2026-10-20T12:00:00+00:00"),
        ])
    (data / "bandeira.csv").write_text(
        "nsu;estabelecimento;valor;data_hora;situacao;final_cartao\n"
        "par;LOJA;1,00;2026-10-19T02:55:00+00:00;APROVADA;1234\n", encoding="utf-8")
    result = server.conciliar(arquivo_csv="bandeira.csv", data="2026-10-19")
    assert result["summary"]["bank_total"] == 1
    assert result["summary"]["csv_total"] == 0
    assert result["summary"]["matched_pairs"] == 1
    assert [d["type"] for d in result["divergences"]] == ["date_mismatch"]
    assert result["divergences"][0]["csv"]["nsu"] == "par"
