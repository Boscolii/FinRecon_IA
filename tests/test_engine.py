from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conciliacao import engine
from conciliacao.evaluate import evaluate
from conciliacao.generator import generate

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def tx(source, nsu="1", merchant="MERCADO", cents=1000, minutes=0, status="approved", last4="1234"):
    return engine.Tx(source, nsu, merchant, cents, T0 + timedelta(minutes=minutes), status, last4)


def kinds(result):
    return sorted(d.type for d in result["divergences"])


def test_clean_match_has_no_divergence():
    result = engine.reconcile([tx("bank")], [tx("csv")])
    assert result["divergences"] == []
    assert result["summary"]["clean_pairs"] == 1


def test_value_mismatch_reports_signed_diff_in_cents():
    result = engine.reconcile([tx("bank", cents=1000)], [tx("csv", cents=1001)])
    assert kinds(result) == ["value_mismatch"]
    assert result["divergences"][0].diff_cents == 1


def test_tolerance_in_cents_absorbs_small_difference():
    result = engine.reconcile([tx("bank", cents=1000)], [tx("csv", cents=1001)], tol_cents=1)
    assert result["divergences"] == []


def test_only_in_bank_and_only_in_csv():
    result = engine.reconcile([tx("bank", nsu="a")], [tx("csv", nsu="b", cents=555)])
    assert kinds(result) == ["only_in_bank", "only_in_csv"]


def test_duplicate_in_csv_and_in_bank():
    csv_dup = engine.reconcile([tx("bank")], [tx("csv"), tx("csv")])
    bank_dup = engine.reconcile([tx("bank"), tx("bank")], [tx("csv")])
    assert kinds(csv_dup) == ["duplicate"]
    assert kinds(bank_dup) == ["duplicate"]
    assert csv_dup["summary"]["matched_pairs"] == 0
    assert bank_dup["summary"]["matched_pairs"] == 0
    assert len(csv_dup["divergences"][0].csv) == 2
    assert len(bank_dup["divergences"][0].bank) == 2


def test_duplicate_nsu_on_both_sides_is_one_unpaired_group():
    result = engine.reconcile(
        [tx("bank", cents=1000), tx("bank", cents=3000)],
        [tx("csv", cents=1001), tx("csv", cents=3000)],
    )
    assert kinds(result) == ["duplicate"]
    assert result["summary"]["matched_pairs"] == 0
    assert result["summary"]["only_in_bank_gross_cents"] == 0
    assert result["summary"]["only_in_csv_gross_cents"] == 0


def test_date_mismatch_only_beyond_tolerance():
    assert engine.reconcile([tx("bank")], [tx("csv", minutes=5)])["divergences"] == []
    assert kinds(engine.reconcile([tx("bank")], [tx("csv", minutes=120)])) == ["date_mismatch"]


def test_status_mismatch():
    result = engine.reconcile([tx("bank")], [tx("csv", status="reversed")])
    assert kinds(result) == ["status_mismatch"]


def test_composite_match_when_csv_has_no_nsu():
    result = engine.reconcile([tx("bank", nsu="abc")], [tx("csv", nsu="")])
    assert result["divergences"] == []
    assert result["summary"]["matched_pairs"] == 1


def test_composite_match_detects_value_divergence_without_nsu():
    result = engine.reconcile([tx("bank", nsu="abc", cents=1000)], [tx("csv", nsu="", cents=1001)])
    assert kinds(result) == ["value_mismatch"]


def test_ambiguous_composite_candidates_go_to_manual_review():
    bank = [tx("bank", nsu="a"), tx("bank", nsu="b", minutes=3)]
    csv_rows = [tx("csv", nsu="")]
    result = engine.reconcile(bank, csv_rows)
    assert kinds(result) == ["manual_review"]
    assert len(result["divergences"][0].bank) == 2


def test_multiple_divergences_on_same_pair():
    result = engine.reconcile([tx("bank", cents=1000)], [tx("csv", cents=1050, status="reversed", minutes=90)])
    assert kinds(result) == ["date_mismatch", "status_mismatch", "value_mismatch"]


def test_summary_separates_value_difference_from_missing_gross_amounts():
    result = engine.reconcile([tx("bank", nsu="a", cents=1000), tx("bank", nsu="b", cents=500)], [tx("csv", nsu="a", cents=990)])
    assert result["summary"]["amount_difference_abs_cents"] == 10
    assert result["summary"]["only_in_bank_gross_cents"] == 500
    assert "impact_cents" not in result["summary"]


@pytest.mark.parametrize("raw,expected", [("12,34", 1234), ("1.234,56", 123456), ("0,05", 5), ("10", 1000), ("19.90", 1990)])
def test_parse_amount_is_exact(raw, expected):
    assert engine.parse_amount(raw) == expected


def test_parse_amount_rejects_sub_cent_values():
    with pytest.raises(ValueError):
        engine.parse_amount("1,005")


def test_parse_dt_normalizes_to_utc():
    assert engine.parse_dt("2026-10-19T09:00:00-03:00") == datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def test_parse_dt_rejects_timestamp_without_timezone():
    with pytest.raises(ValueError, match="fuso"):
        engine.parse_dt("2026-10-19T12:00:00")


def test_day_window_uses_sao_paulo_business_day():
    start, end = engine.day_window("2026-10-19")
    assert start == datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("kwargs", [{"tol_cents": -1}, {"tol_minutes": -1}, {"tol_cents": 1.5}])
def test_reconcile_rejects_invalid_tolerances(kwargs):
    with pytest.raises(ValueError):
        engine.reconcile([tx("bank")], [tx("csv")], **kwargs)


def test_load_csv_reports_header_and_row_errors(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("nsu;estabelecimento;valor;data_hora;situacao\n1;Loja;abc;2026-10-19T12:00:00;INVALIDA\n", encoding="utf-8")
    with pytest.raises(engine.CsvValidationError) as exc:
        engine.load_csv(path)
    assert any("cabecalho" in error for error in exc.value.errors)


def test_load_csv_collects_multiple_invalid_rows(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text(
        "nsu;estabelecimento;valor;data_hora;situacao;final_cartao\n"
        "1;Loja;abc;2026-10-19T12:00:00;APROVADA;1234\n"
        "2;;10,00;2026-10-19T12:00:00-03:00;INVALIDA;abcd\n",
        encoding="utf-8",
    )
    with pytest.raises(engine.CsvValidationError) as exc:
        engine.load_csv(path)
    assert len(exc.value.errors) == 2


def test_synthetic_dataset_is_fully_recovered(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=500, per_type=5, benign=5, seed=11)
    metrics = evaluate(db, csv_path, truth, "2026-10-19")
    assert metrics["overall"]["precision"] == 1.0
    assert metrics["overall"]["recall"] == 1.0
    assert metrics["overall"]["expected"] == 30


def test_evaluation_uses_individual_finding_ids(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=100, per_type=2, benign=2, seed=11)
    entries = __import__("json").loads(Path(truth).read_text(encoding="utf-8"))
    assert all("finding_id" in item for item in entries)


def test_bank_connection_is_read_only(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=100, per_type=2, benign=2)
    conn = engine.connect_readonly(db)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM transactions")
