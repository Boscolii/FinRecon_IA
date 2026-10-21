import argparse
import json
from pathlib import Path

from . import engine


def evaluate(db_path, csv_path, truth_path, day, margin_days=1):
    start, end = engine.day_window(day, margin_days)
    conn = engine.connect_readonly(db_path)
    bank = engine.load_bank(conn, start, end)
    conn.close()
    csv_rows = engine.load_csv(csv_path, start, end)
    result = engine.reconcile(bank, csv_rows)
    found = {(d.type, d.finding_id) for d in result["divergences"]}
    truth_rows = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    if any("finding_id" not in row for row in truth_rows):
        raise ValueError("gabarito sem finding_id; gere novamente os dados sinteticos")
    truth = {(t["type"], t["finding_id"]) for t in truth_rows}
    metrics = {}
    for kind in sorted({k for k, _ in truth | found}):
        f = {x for x in found if x[0] == kind}
        t = {x for x in truth if x[0] == kind}
        tp = len(f & t)
        metrics[kind] = {"expected": len(t), "found": len(f), "true_positive": tp, "precision": tp / len(f) if f else None, "recall": tp / len(t) if t else None}
    tp = len(found & truth)
    metrics["overall"] = {"expected": len(truth), "found": len(found), "true_positive": tp, "precision": tp / len(found) if found else None, "recall": tp / len(truth) if truth else None}
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data")
    parser.add_argument("--day", default="2026-10-19")
    args = parser.parse_args()
    data = Path(args.data)
    metrics = evaluate(data / "banco.db", data / "bandeira.csv", data / "gabarito.json", args.day)
    print(f"{'tipo':<18}{'esperado':>9}{'achado':>8}{'precisao':>10}{'recall':>8}")
    for kind, m in metrics.items():
        precision = "n/a" if m["precision"] is None else f"{m['precision']:.2%}"
        recall = "n/a" if m["recall"] is None else f"{m['recall']:.2%}"
        print(f"{kind:<18}{m['expected']:>9}{m['found']:>8}{precision:>10}{recall:>8}")


if __name__ == "__main__":
    main()
