import argparse
import csv
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

MERCHANTS = ["PADARIA SAO JORGE", "MERCADO CENTRAL", "POSTO IPIRANGA", "FARMACIA POPULAR", "UBER TRIP", "IFOOD", "NETFLIX", "SPOTIFY", "AMAZON BR", "MAGAZINE LUIZA", "RESTAURANTE SABOR", "LIVRARIA CULTURA", "CINEMARK", "DROGASIL", "AMERICANAS"]
LOCAL_TZ = timezone(timedelta(hours=-3))
SCHEMA = """
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nsu TEXT NOT NULL,
    merchant TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL,
    card_last4 TEXT NOT NULL
);
CREATE INDEX idx_tx_occurred ON transactions(occurred_at);
CREATE INDEX idx_tx_nsu ON transactions(nsu);
"""
CSV_HEADER = ["nsu", "estabelecimento", "valor", "data_hora", "situacao", "final_cartao"]
INJECTED = ("value_mismatch", "only_in_bank", "duplicate", "date_mismatch", "status_mismatch")


def _fmt_amount(cents):
    return f"{cents // 100},{cents % 100:02d}"


def _csv_row(tx, nsu=None):
    return {
        "nsu": tx["nsu"] if nsu is None else nsu,
        "estabelecimento": tx["merchant"],
        "valor": _fmt_amount(tx["amount_cents"]),
        "data_hora": tx["occurred_at"].astimezone(LOCAL_TZ).isoformat(),
        "situacao": "APROVADA" if tx["status"] == "approved" else "ESTORNADA",
        "final_cartao": tx["card_last4"],
    }


def generate(db_path, csv_path, truth_path, n=2000, per_type=10, benign=10, seed=7, day="2026-10-19"):
    rng = random.Random(seed)
    base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    seen, txs = set(), []
    while len(txs) < n:
        tx = {
            "nsu": f"{rng.randrange(10**11, 10**12)}",
            "merchant": rng.choice(MERCHANTS),
            "amount_cents": rng.randint(150, 90000),
            "occurred_at": base + timedelta(seconds=rng.randint(6 * 3600, 20 * 3600)),
            "status": "reversed" if rng.random() < 0.03 else "approved",
            "card_last4": f"{rng.randrange(10000):04d}",
        }
        composite = (tx["amount_cents"], tx["merchant"], tx["card_last4"])
        if composite in seen or any(t["nsu"] == tx["nsu"] for t in txs[-50:]):
            continue
        seen.add(composite)
        txs.append(tx)

    picked = rng.sample(range(n), per_type * len(INJECTED) + benign)
    groups = {name: picked[i * per_type:(i + 1) * per_type] for i, name in enumerate(INJECTED)}
    benign_idx = set(picked[per_type * len(INJECTED):])

    bank_rows = [dict(t) for t in txs]
    csv_rows = {i: _csv_row(t, nsu="" if i in benign_idx else None) for i, t in enumerate(txs)}
    truth = []

    for i in groups["value_mismatch"]:
        delta = rng.choice([-1, 1]) * rng.randint(1, 99)
        csv_rows[i]["valor"] = _fmt_amount(txs[i]["amount_cents"] + delta)
        truth.append({"type": "value_mismatch", "nsu": txs[i]["nsu"], "finding_id": f"value_mismatch:nsu:{txs[i]['nsu']}"})
    for i in groups["only_in_bank"]:
        del csv_rows[i]
        truth.append({"type": "only_in_bank", "nsu": txs[i]["nsu"], "finding_id": f"only_in_bank:nsu:{txs[i]['nsu']}"})
    for i in groups["date_mismatch"]:
        shifted = txs[i]["occurred_at"] + timedelta(hours=2)
        csv_rows[i]["data_hora"] = shifted.astimezone(LOCAL_TZ).isoformat()
        truth.append({"type": "date_mismatch", "nsu": txs[i]["nsu"], "finding_id": f"date_mismatch:nsu:{txs[i]['nsu']}"})
    for i in groups["status_mismatch"]:
        csv_rows[i]["situacao"] = "ESTORNADA" if txs[i]["status"] == "approved" else "APROVADA"
        truth.append({"type": "status_mismatch", "nsu": txs[i]["nsu"], "finding_id": f"status_mismatch:nsu:{txs[i]['nsu']}"})

    extra_csv = []
    for pos, i in enumerate(groups["duplicate"]):
        (bank_rows if pos % 2 == 0 else extra_csv).append(dict(txs[i]) if pos % 2 == 0 else csv_rows[i])
        truth.append({"type": "duplicate", "nsu": txs[i]["nsu"], "finding_id": f"duplicate:nsu:{txs[i]['nsu']}"})
    for k in range(per_type):
        tx = {
            "nsu": f"9{k:011d}",
            "merchant": rng.choice(MERCHANTS),
            "amount_cents": rng.randint(150, 90000),
            "occurred_at": base + timedelta(seconds=rng.randint(6 * 3600, 20 * 3600)),
            "status": "approved",
            "card_last4": f"{rng.randrange(10000):04d}",
        }
        extra_csv.append(_csv_row(tx))
        truth.append({"type": "only_in_csv", "nsu": tx["nsu"], "finding_id": f"only_in_csv:nsu:{tx['nsu']}"})

    for path in (db_path, csv_path, truth_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(db_path).unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO transactions (nsu, merchant, amount_cents, occurred_at, status, card_last4) VALUES (?, ?, ?, ?, ?, ?)",
        [(t["nsu"], t["merchant"], t["amount_cents"], t["occurred_at"].isoformat(), t["status"], t["card_last4"]) for t in bank_rows],
    )
    conn.commit()
    conn.close()

    all_csv = list(csv_rows.values()) + extra_csv
    rng.shuffle(all_csv)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADER, delimiter=";")
        writer.writeheader()
        writer.writerows(all_csv)
    Path(truth_path).write_text(json.dumps(sorted(truth, key=lambda t: (t["type"], t["nsu"])), indent=2), encoding="utf-8")
    return {"bank_rows": len(bank_rows), "csv_rows": len(all_csv), "injected": len(truth), "day": day}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data")
    parser.add_argument("--n", type=int, default=2000)
    parser.add_argument("--per-type", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--day", default="2026-10-19")
    args = parser.parse_args()
    out = Path(args.out)
    print(generate(out / "banco.db", out / "bandeira.csv", out / "gabarito.json", args.n, args.per_type, seed=args.seed, day=args.day))


if __name__ == "__main__":
    main()
