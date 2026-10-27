import argparse
import json
from collections import Counter
from pathlib import Path

from . import engine


def _evidence_rows(value):
    if value is None:
        return ()
    rows = value if isinstance(value, list) else [value]
    return tuple(sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows))


def _evidence_key(kind, bank, csv):
    return kind, _evidence_rows(bank), _evidence_rows(csv)


def evaluate(db_path, csv_path, truth_path, day, margin_days=1):
    start, end = engine.day_window(day, margin_days)
    conn = engine.connect_readonly(db_path)
    bank = engine.load_bank(conn, start, end)
    conn.close()
    csv_rows = engine.load_csv(csv_path, start, end)
    result = engine.reconcile(bank, csv_rows)
    found = Counter(_evidence_key(d.type, d.bank, d.csv) for d in result["divergences"])
    truth_rows = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    if any("bank" not in row or "csv" not in row for row in truth_rows):
        raise ValueError("gabarito antigo sem evidencias; gere novos dados sinteticos em uma pasta separada")
    truth = Counter(_evidence_key(t["type"], t["bank"], t["csv"]) for t in truth_rows)
    metrics = {}
    def score(f, t):
        tp = sum((f & t).values())
        predicted, expected = sum(f.values()), sum(t.values())
        return {"expected": expected, "found": predicted, "true_positive": tp, "false_positive": predicted - tp, "false_negative": expected - tp, "precision": tp / predicted if predicted else None, "recall": tp / expected if expected else None}
    for kind in sorted({key[0] for key in truth.keys() | found.keys()}):
        metrics[kind] = score(Counter({k: v for k, v in found.items() if k[0] == kind}), Counter({k: v for k, v in truth.items() if k[0] == kind}))
    metrics["overall"] = score(found, truth)
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data")
    parser.add_argument("--day", default="2026-10-19")
    args = parser.parse_args()
    data = Path(args.data)
    metrics = evaluate(data / "banco.db", data / "bandeira.csv", data / "gabarito.json", args.day)
    print(f"{'tipo':<18}{'esperado':>9}{'achado':>8}{'TP':>6}{'FP':>6}{'FN':>6}{'precisao':>10}{'recall':>8}")
    for kind, m in metrics.items():
        precision = "n/a" if m["precision"] is None else f"{m['precision']:.2%}"
        recall = "n/a" if m["recall"] is None else f"{m['recall']:.2%}"
        print(f"{kind:<18}{m['expected']:>9}{m['found']:>8}{m['true_positive']:>6}{m['false_positive']:>6}{m['false_negative']:>6}{precision:>10}{recall:>8}")


if __name__ == "__main__":
    main()
