from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import sqlite3
import random

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


def test_duplicate_only_in_bank_is_not_paired_with_csv_without_nsu():
    result = engine.reconcile([tx("bank"), tx("bank"), tx("bank")], [tx("csv", nsu="")])
    assert kinds(result) == ["duplicate", "manual_review"]
    duplicate = next(d for d in result["divergences"] if d.type == "duplicate")
    assert len(duplicate.bank) == 3
    assert duplicate.csv is None
    assert result["summary"]["matched_pairs"] == 0
    assert result["summary"]["only_in_bank_gross_cents"] == 0
    assert result["summary"]["only_in_csv_gross_cents"] == 0


def test_duplicate_only_in_csv_is_reviewed_as_one_group():
    result = engine.reconcile([], [tx("csv"), tx("csv"), tx("csv")])
    assert kinds(result) == ["duplicate"]
    assert len(result["divergences"][0].csv) == 3
    assert result["divergences"][0].bank is None
    assert result["summary"]["only_in_csv_gross_cents"] == 0


def test_duplicate_and_unique_bank_candidate_remain_under_review():
    bank = [tx("bank", nsu="dup"), tx("bank", nsu="dup"), tx("bank", nsu="unique")]
    result = engine.reconcile(bank, [tx("csv", nsu="")])
    assert kinds(result) == ["duplicate", "manual_review"]
    review = next(d for d in result["divergences"] if d.type == "manual_review")
    assert {record["nsu"] for record in review.bank} == {"dup", "unique"}
    assert result["summary"]["only_in_bank_gross_cents"] == 0


def test_ambiguity_connected_to_duplicate_propagates_to_other_csv_candidates():
    bank = [tx("bank", nsu="dup", minutes=0), tx("bank", nsu="dup", minutes=0), tx("bank", nsu="unique", minutes=1200)]
    csv_rows = [tx("csv", nsu="", minutes=100), tx("csv", nsu="", minutes=2500)]
    result = engine.reconcile(bank, csv_rows)
    assert kinds(result) == ["duplicate", "manual_review"]
    review = next(d for d in result["divergences"] if d.type == "manual_review")
    assert len(review.csv) == 2
    assert {record["nsu"] for record in review.bank} == {"dup", "unique"}


def test_date_mismatch_only_beyond_tolerance():
    assert engine.reconcile([tx("bank")], [tx("csv", minutes=5)])["divergences"] == []
    assert kinds(engine.reconcile([tx("bank")], [tx("csv", minutes=120)])) == ["date_mismatch"]


def test_status_mismatch():
    result = engine.reconcile([tx("bank")], [tx("csv", status="reversed")])
    assert kinds(result) == ["status_mismatch"]


def test_same_nsu_different_merchant_and_card_requires_review():
    result = engine.reconcile([tx("bank")], [tx("csv", merchant="OUTRA", last4="9999")])
    assert kinds(result) == ["manual_review"]
    assert result["summary"]["clean_pairs"] == 0
    assert "estabelecimento" in result["divergences"][0].detail
    assert "cartao" in result["divergences"][0].detail


def test_target_day_excludes_unrelated_neighbors_but_keeps_cross_day_pair():
    window = engine.day_window("2026-10-19")
    bank = [tx("bank", nsu="inside"), tx("bank", nsu="outside", minutes=1440)]
    csv_rows = [tx("csv", nsu="inside", minutes=-600), tx("csv", nsu="other", minutes=1440)]
    result = engine.reconcile(bank, csv_rows, target_window=window)
    assert result["summary"]["bank_total"] == 1
    assert result["summary"]["csv_total"] == 0
    assert result["summary"]["matched_pairs"] == 1
    assert kinds(result) == ["date_mismatch"]
    assert result["divergences"][0].nsu == "inside"


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


def test_composite_components_exclude_distant_unrelated_records():
    bank = [tx("bank", nsu="a"), tx("bank", nsu="b", minutes=3 * 1440)]
    result = engine.reconcile(bank, [tx("csv", nsu="")])
    assert result["summary"]["matched_pairs"] == 1
    assert kinds(result) == ["only_in_bank"]
    assert result["divergences"][0].nsu == "b"


def test_optimized_components_match_brute_force_candidates():
    rng = random.Random(19)
    bank = [tx("bank", nsu=str(i), minutes=rng.randint(-4000, 4000), last4=rng.choice(["1234", "5678"])) for i in range(60)]
    csv_rows = [tx("csv", nsu="", minutes=rng.randint(-4000, 4000), last4=rng.choice(["1234", "5678"])) for _ in range(70)]
    components = list(engine._composite_components(bank, csv_rows))
    for component in components:
        b_indexes, c_indexes = component["bank"], component["csv"]
        expected = sum(1 for bi in b_indexes for ci in c_indexes
                       if bank[bi].merchant == csv_rows[ci].merchant
                       and bank[bi].card_last4 == csv_rows[ci].card_last4
                       and abs(bank[bi].occurred_at - csv_rows[ci].occurred_at) <= engine.COMPOSITE_WINDOW)
        assert component["candidate_count"] == expected
    component_by_bank = {bi: i for i, group in enumerate(components) for bi in group["bank"]}
    component_by_csv = {ci: i for i, group in enumerate(components) for ci in group["csv"]}
    for bi, b in enumerate(bank):
        for ci, c in enumerate(csv_rows):
            if b.merchant == c.merchant and b.card_last4 == c.card_last4 and abs(b.occurred_at - c.occurred_at) <= engine.COMPOSITE_WINDOW:
                assert component_by_bank[bi] == component_by_csv[ci]
    for group in components:
        # Cada grupo deve ser conexo; nao deve unir candidatos independentes.
        reached_b, reached_c = set(), set()
        pending = [("b", group["bank"][0])] if group["bank"] else [("c", group["csv"][0])]
        while pending:
            side, index = pending.pop()
            if side == "b" and index not in reached_b:
                reached_b.add(index)
                pending.extend(("c", ci) for ci in group["csv"] if bank[index].card_last4 == csv_rows[ci].card_last4 and abs(bank[index].occurred_at - csv_rows[ci].occurred_at) <= engine.COMPOSITE_WINDOW)
            elif side == "c" and index not in reached_c:
                reached_c.add(index)
                pending.extend(("b", bi) for bi in group["bank"] if bank[bi].card_last4 == csv_rows[index].card_last4 and abs(bank[bi].occurred_at - csv_rows[index].occurred_at) <= engine.COMPOSITE_WINDOW)
        assert reached_b == set(group["bank"]) and reached_c == set(group["csv"])


def test_large_ambiguous_group_preserves_all_evidence_without_pairing():
    count = 5000
    bank = [tx("bank", nsu=str(i)) for i in range(count)]
    csv_rows = [tx("csv", nsu="") for _ in range(count)]
    result = engine.reconcile(bank, csv_rows)
    assert kinds(result) == ["manual_review"]
    finding = result["divergences"][0]
    assert len(finding.bank) == len(finding.csv) == count
    assert "25000000 candidato(s)" in finding.detail
    assert result["summary"]["matched_pairs"] == 0


def test_composite_window_includes_exact_24_hour_boundary():
    result = engine.reconcile([tx("bank")], [tx("csv", nsu="", minutes=1440)])
    assert kinds(result) == ["date_mismatch"]


def test_identical_missing_records_have_distinct_stable_ids():
    records = [tx("bank", nsu=""), tx("bank", nsu="")]
    first = engine.reconcile(records, [])
    second = engine.reconcile(records, [])
    ids = [d.finding_id for d in first["divergences"]]
    assert len(set(ids)) == 2
    assert ids == [d.finding_id for d in second["divergences"]]


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


@pytest.mark.parametrize("raw,expected", [
    ("92233720368547758,07", 2**63 - 1),
    ("-92233720368547758,08", -(2**63)),
    ("0000000000000000000000000001,02", 102),
])
def test_parse_amount_preserves_exact_cents_at_sqlite_limits(raw, expected):
    assert engine.parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["92233720368547758,08", "-92233720368547758,09", "123456789012345678901234567890,12"])
def test_parse_amount_rejects_values_outside_sqlite_limits(raw):
    with pytest.raises(ValueError, match="intervalo"):
        engine.parse_amount(raw)


def test_parse_amount_rejects_sub_cent_values():
    with pytest.raises(ValueError):
        engine.parse_amount("1,005")


@pytest.mark.parametrize("raw", ["1.23,45", "1.2345,67", "1,234.56", "1e3", "12,", "1..00"])
def test_parse_amount_rejects_malformed_separators(raw):
    with pytest.raises(ValueError):
        engine.parse_amount(raw)


def test_parse_dt_normalizes_to_utc():
    assert engine.parse_dt("2026-10-19T09:00:00-03:00") == datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def test_parse_dt_rejects_timestamp_without_timezone():
    with pytest.raises(ValueError, match="fuso"):
        engine.parse_dt("2026-10-19T12:00:00")


def test_day_window_uses_sao_paulo_business_day():
    start, end = engine.day_window("2026-10-19")
    assert start == datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc)


def test_load_bank_filters_instants_with_different_offsets_and_boundaries():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transactions(id INTEGER PRIMARY KEY, nsu TEXT, merchant TEXT, amount_cents INTEGER, occurred_at TEXT, status TEXT, card_last4 TEXT)")
    for i, timestamp in enumerate(["2026-10-18T23:59:59.999999-03:00", "2026-10-19T00:00:00-03:00", "2026-10-19T01:00:00-03:00", "2026-10-20T02:59:59.999999+00:00", "2026-10-20T03:00:00+00:00"], 1):
        conn.execute("INSERT INTO transactions VALUES (?, ?, 'LOJA', 100, ?, 'approved', '1234')", (i, str(i), timestamp))
    start, end = engine.day_window("2026-10-19")
    assert [row.nsu for row in engine.load_bank(conn, start, end)] == ["2", "3", "4"]
    conn.close()


def test_load_bank_normalizes_null_nsu_before_reconciliation():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transactions(id INTEGER PRIMARY KEY, nsu TEXT, merchant TEXT, amount_cents INTEGER, occurred_at TEXT, status TEXT, card_last4 TEXT)")
    conn.execute("INSERT INTO transactions VALUES (1, NULL, 'LOJA', 100, '2026-10-19T12:00:00+00:00', 'approved', '1234')")
    rows = engine.load_bank(conn, *engine.day_window("2026-10-19"))
    assert rows[0].nsu == ""
    assert kinds(engine.reconcile(rows, [])) == ["only_in_bank"]
    conn.close()


@pytest.mark.parametrize("amount", [100.9, "invalido", None])
def test_load_bank_rejects_noninteger_amount_instead_of_truncating(amount):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transactions(id INTEGER PRIMARY KEY, nsu TEXT, merchant TEXT, amount_cents INTEGER, occurred_at TEXT, status TEXT, card_last4 TEXT)")
    conn.execute("INSERT INTO transactions VALUES (7, '1', 'LOJA', ?, '2026-10-19T12:00:00+00:00', 'approved', '1234')", (amount,))
    with pytest.raises(ValueError, match="id 7.*inteiro em centavos"):
        engine.load_bank(conn, *engine.day_window("2026-10-19"))
    conn.close()


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


def test_load_csv_rejects_extra_and_missing_fields_per_line(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text("nsu;estabelecimento;valor;data_hora;situacao;final_cartao\n"
                    "1;Loja;10,00;2026-10-19T12:00:00-03:00;APROVADA;1234;EXTRA\n"
                    "2;Loja;10,00;2026-10-19T12:00:00-03:00;APROVADA\n", encoding="utf-8")
    with pytest.raises(engine.CsvValidationError) as exc:
        engine.load_csv(path)
    assert len(exc.value.errors) == 2
    assert "linha 2" in exc.value.errors[0] and "linha 3" in exc.value.errors[1]


def test_synthetic_dataset_is_fully_recovered(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=500, per_type=5, benign=5, seed=11)
    metrics = evaluate(db, csv_path, truth, "2026-10-19")
    assert metrics["overall"]["precision"] == 1.0
    assert metrics["overall"]["recall"] == 1.0
    assert metrics["overall"]["expected"] == 30
    assert metrics["overall"]["true_positive"] == 30
    assert metrics["overall"]["false_positive"] == 0
    assert metrics["overall"]["false_negative"] == 0


def test_evaluation_uses_individual_finding_ids(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=100, per_type=2, benign=2, seed=11)
    entries = __import__("json").loads(Path(truth).read_text(encoding="utf-8"))
    assert all("finding_id" in item for item in entries)


def test_evaluation_rejects_wrong_evidence_with_same_type_and_nsu(tmp_path, monkeypatch):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=100, per_type=2, benign=2, seed=11)
    real_reconcile = engine.reconcile

    def incorrect_reconcile(*args, **kwargs):
        result = real_reconcile(*args, **kwargs)
        index = next(i for i, item in enumerate(result["divergences"]) if item.type == "value_mismatch")
        finding = result["divergences"][index]
        result["divergences"][index] = replace(finding, csv={**finding.csv, "amount_cents": finding.csv["amount_cents"] + 1})
        return result

    monkeypatch.setattr(engine, "reconcile", incorrect_reconcile)
    metrics = evaluate(db, csv_path, truth, "2026-10-19")["value_mismatch"]
    assert (metrics["true_positive"], metrics["false_positive"], metrics["false_negative"]) == (1, 1, 1)


def test_bank_connection_is_read_only(tmp_path):
    db, csv_path, truth = tmp_path / "b.db", tmp_path / "c.csv", tmp_path / "t.json"
    generate(db, csv_path, truth, n=100, per_type=2, benign=2)
    conn = engine.connect_readonly(db)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM transactions")


def test_bank_connection_escapes_uri_characters_and_stays_readonly(tmp_path):
    db = tmp_path / "banco #1?.db"
    # '?' is not a legal filename on Windows; '#' exercises URI escaping there.
    if __import__("os").name == "nt":
        db = tmp_path / "banco #1.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE example (value INTEGER)")
        conn.execute("INSERT INTO example VALUES (42)")
    conn = engine.connect_readonly(db)
    try:
        assert conn.execute("SELECT value FROM example").fetchone() == (42,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM example")
    finally:
        conn.close()
