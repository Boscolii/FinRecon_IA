import pytest

from conciliacao import engine
from conciliacao.demo_manual import create_demo


def test_manual_demo_matches_documented_findings(tmp_path):
    data = create_demo(tmp_path / "demo")
    start, end = engine.day_window("2026-10-19")
    conn = engine.connect_readonly(data / "banco.db")
    try:
        bank = engine.load_bank(conn, start, end)
    finally:
        conn.close()
    csv_rows = engine.load_csv(data / "bandeira.csv", start, end)
    result = engine.reconcile(bank, csv_rows)
    assert result["summary"]["bank_total"] == 7
    assert result["summary"]["csv_total"] == 5
    assert result["summary"]["matched_pairs"] == 2
    assert result["summary"]["clean_pairs"] == 1
    assert sorted(item.type for item in result["divergences"]) == ["duplicate", "manual_review", "only_in_bank", "only_in_csv", "value_mismatch"]


def test_manual_demo_never_overwrites_existing_directory(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "banco.db"
    marker.write_bytes(b"user data")
    with pytest.raises(FileExistsError):
        create_demo(existing)
    assert marker.read_bytes() == b"user data"
