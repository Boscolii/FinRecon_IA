import csv
import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

STATUS_MAP = {"APROVADA": "approved", "ESTORNADA": "reversed", "APPROVED": "approved", "REVERSED": "reversed"}
COMPOSITE_WINDOW = timedelta(hours=24)
BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")
CSV_HEADER = ("nsu", "estabelecimento", "valor", "data_hora", "situacao", "final_cartao")
TYPES = ("value_mismatch", "only_in_bank", "only_in_csv", "duplicate", "date_mismatch", "status_mismatch", "manual_review")
MAX_TOL_CENTS, MAX_TOL_MINUTES = 1_000_000, 1_440


class CsvValidationError(ValueError):
    """CSV rejected as a whole; no partially validated data is returned."""
    def __init__(self, errors):
        self.errors = errors
        super().__init__("CSV invalido: " + "; ".join(errors))


@dataclass(frozen=True)
class Tx:
    source: str
    nsu: str
    merchant: str
    amount_cents: int
    occurred_at: datetime
    status: str
    card_last4: str

    def to_dict(self):
        return {**asdict(self), "occurred_at": self.occurred_at.isoformat()}


@dataclass(frozen=True)
class Divergence:
    type: str
    nsu: str
    bank: object | None
    csv: object | None
    diff_cents: int
    detail: str
    finding_id: str

    def to_dict(self):
        return asdict(self)


def parse_amount(raw):
    text = str(raw).strip()
    if not text:
        raise ValueError("valor em branco")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    cents = Decimal(text) * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"valor com mais de duas casas decimais: {raw}")
    return int(cents)


def parse_dt(raw):
    try:
        dt = datetime.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"data/hora invalida: {raw}") from exc
    if dt.tzinfo is None:
        raise ValueError("data_hora deve incluir fuso horario")
    return dt.astimezone(timezone.utc)


def norm_merchant(name):
    value = " ".join(str(name).upper().split())
    if not value:
        raise ValueError("estabelecimento em branco")
    return value


def norm_status(raw):
    key = str(raw).strip().upper()
    if key not in STATUS_MAP:
        raise ValueError(f"status desconhecido: {raw}")
    return STATUS_MAP[key]


def _nonnegative_int(name, value, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ValueError(f"{name} deve ser inteiro entre 0 e {maximum}")
    return value


def day_window(day, margin_days=0):
    margin_days = _nonnegative_int("margem_dias", margin_days, 31)
    try:
        business_date = date.fromisoformat(day)
    except (TypeError, ValueError) as exc:
        raise ValueError("data deve usar o formato YYYY-MM-DD") from exc
    start_local = datetime.combine(business_date, datetime.min.time(), BUSINESS_TZ) - timedelta(days=margin_days)
    end_local = start_local + timedelta(days=1 + 2 * margin_days)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def load_bank(conn, start, end):
    rows = conn.execute("SELECT nsu, merchant, amount_cents, occurred_at, status, card_last4 FROM transactions WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id", (start.isoformat(), end.isoformat())).fetchall()
    return [Tx("bank", r[0], norm_merchant(r[1]), int(r[2]), parse_dt(r[3]), norm_status(r[4]), r[5]) for r in rows]


def load_csv(path, start=None, end=None):
    errors, out = [], []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        actual = tuple(reader.fieldnames or ())
        if not actual:
            raise CsvValidationError(["cabecalho ausente ou CSV vazio"])
        missing, extra = [x for x in CSV_HEADER if x not in actual], [x for x in actual if x not in CSV_HEADER]
        duplicate_headers = sorted({name for name in actual if actual.count(name) > 1})
        if missing or extra or duplicate_headers:
            parts = (["colunas ausentes: " + ", ".join(missing)] if missing else []) + (["colunas inesperadas: " + ", ".join(extra)] if extra else [])
            if duplicate_headers:
                parts.append("colunas duplicadas: " + ", ".join(duplicate_headers))
            raise CsvValidationError(["cabecalho invalido (" + "; ".join(parts) + ")"])
        for line, row in enumerate(reader, start=2):
            try:
                if None in row.values():
                    raise ValueError("numero de colunas invalido")
                for field in ("estabelecimento", "valor", "data_hora", "situacao", "final_cartao"):
                    if not str(row[field]).strip():
                        raise ValueError(f"{field} em branco")
                last4 = row["final_cartao"].strip()
                if not (len(last4) == 4 and last4.isdigit()):
                    raise ValueError("final_cartao deve conter exatamente 4 digitos")
                tx = Tx("csv", row["nsu"].strip(), norm_merchant(row["estabelecimento"]), parse_amount(row["valor"]), parse_dt(row["data_hora"]), norm_status(row["situacao"]), last4)
                if (start is None or tx.occurred_at >= start) and (end is None or tx.occurred_at < end):
                    out.append(tx)
            except (KeyError, ValueError, ArithmeticError) as exc:
                errors.append(f"linha {line}: {exc}")
    if errors:
        raise CsvValidationError(errors)
    return out


def _group(items, key):
    groups = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return groups


def _tx_key(tx):
    return "|".join((tx.source, tx.nsu, tx.merchant, str(tx.amount_cents), tx.occurred_at.isoformat(), tx.status, tx.card_last4))


def finding_id(kind, bank=None, csv=None, group_key=""):
    parts = [kind, group_key] + sorted(_tx_key(tx) for tx in (bank or [])) + sorted(_tx_key(tx) for tx in (csv or []))
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _divergence(kind, nsu, bank=None, csv=None, diff_cents=0, detail="", group_key=""):
    bank_rows, csv_rows = list(bank or []), list(csv or [])
    stable_id = f"{kind}:nsu:{nsu}" if nsu else finding_id(kind, bank_rows, csv_rows, group_key)
    return Divergence(kind, nsu, bank_rows[0].to_dict() if len(bank_rows) == 1 else [tx.to_dict() for tx in bank_rows] or None, csv_rows[0].to_dict() if len(csv_rows) == 1 else [tx.to_dict() for tx in csv_rows] or None, diff_cents, detail, stable_id)


def _compare(bank, other, tol_cents, tol_minutes):
    found, diff = [], other.amount_cents - bank.amount_cents
    if abs(diff) > tol_cents:
        found.append(_divergence("value_mismatch", bank.nsu, [bank], [other], diff, f"banco {bank.amount_cents} centavos, csv {other.amount_cents} centavos"))
    if bank.status != other.status:
        found.append(_divergence("status_mismatch", bank.nsu, [bank], [other], detail=f"banco {bank.status}, csv {other.status}"))
    delta = abs(other.occurred_at - bank.occurred_at)
    if delta > timedelta(minutes=tol_minutes):
        found.append(_divergence("date_mismatch", bank.nsu, [bank], [other], detail=f"diferenca de {int(delta.total_seconds() // 60)} minutos"))
    return found


def reconcile(bank, csv_rows, tol_cents=0, tol_minutes=5):
    tol_cents, tol_minutes = _nonnegative_int("tolerancia_centavos", tol_cents, MAX_TOL_CENTS), _nonnegative_int("tolerancia_minutos", tol_minutes, MAX_TOL_MINUTES)
    divergences, pairs, duplicate_bank = [], [], []
    bank_by_nsu, csv_by_nsu = _group([t for t in bank if t.nsu], lambda t: t.nsu), _group([t for t in csv_rows if t.nsu], lambda t: t.nsu)
    bank_left, csv_left, csv_unmatched_id = [t for t in bank if not t.nsu], [t for t in csv_rows if not t.nsu], []
    for nsu in sorted(set(bank_by_nsu) | set(csv_by_nsu)):
        b_rows, c_rows = bank_by_nsu.get(nsu, []), csv_by_nsu.get(nsu, [])
        if len(b_rows) > 1 or len(c_rows) > 1:
            divergences.append(_divergence("duplicate", nsu, b_rows, c_rows, detail=f"NSU {nsu} nao e unico: {len(b_rows)} no banco e {len(c_rows)} no csv; revisao manual necessaria", group_key=nsu))
            duplicate_bank.extend(b_rows)
        elif len(b_rows) == len(c_rows) == 1:
            pairs.append((b_rows[0], c_rows[0]))
        elif b_rows:
            bank_left.extend(b_rows)
        else:
            csv_unmatched_id.extend(c_rows)
    remaining_csv = []
    for tx in csv_left:
        candidates = [b for b in duplicate_bank if (b.merchant, b.card_last4) == (tx.merchant, tx.card_last4) and abs(b.occurred_at - tx.occurred_at) <= COMPOSITE_WINDOW]
        if candidates:
            divergences.append(_divergence("manual_review", "", candidates, [tx], detail="CSV sem NSU pode corresponder a grupo duplicado no banco; revisao manual necessaria"))
        else:
            remaining_csv.append(tx)
    b_groups, c_groups = _group(bank_left, lambda t: (t.merchant, t.card_last4)), _group(remaining_csv, lambda t: (t.merchant, t.card_last4))
    unmatched_bank, unmatched_csv = [], list(csv_unmatched_id)
    for key in sorted(set(b_groups) | set(c_groups)):
        b_rows, c_rows = b_groups.get(key, []), c_groups.get(key, [])
        candidates = [(b, c) for b in b_rows for c in c_rows if abs(b.occurred_at - c.occurred_at) <= COMPOSITE_WINDOW]
        if len(b_rows) == len(c_rows) == 1 and candidates:
            pairs.append(candidates[0])
        elif candidates:
            divergences.append(_divergence("manual_review", "", b_rows, c_rows, detail=f"{len(candidates)} candidato(s) composto(s) para estabelecimento/cartao; nenhum pareamento automatico foi feito", group_key="|".join(key)))
        else:
            unmatched_bank.extend(b_rows)
            unmatched_csv.extend(c_rows)
    for tx in unmatched_bank:
        divergences.append(_divergence("only_in_bank", tx.nsu, [tx], detail="transacao ausente no csv"))
    for tx in unmatched_csv:
        divergences.append(_divergence("only_in_csv", tx.nsu, csv=[tx], detail="transacao ausente no banco"))
    clean = 0
    for b_tx, c_tx in pairs:
        found = _compare(b_tx, c_tx, tol_cents, tol_minutes)
        divergences.extend(found)
        clean += not found
    divergences.sort(key=lambda d: (d.type, d.nsu, d.finding_id))
    return {"summary": summarize(bank, csv_rows, divergences, len(pairs), clean), "divergences": divergences}


def summarize(bank, csv_rows, divergences, matched, clean):
    counts = {kind: 0 for kind in TYPES}
    amount_difference = only_bank = only_csv = 0
    for d in divergences:
        counts[d.type] += 1
        if d.type == "value_mismatch": amount_difference += abs(d.diff_cents)
        elif d.type == "only_in_bank": only_bank += d.bank["amount_cents"]
        elif d.type == "only_in_csv": only_csv += d.csv["amount_cents"]
    return {"bank_total": len(bank), "csv_total": len(csv_rows), "matched_pairs": matched, "clean_pairs": clean, "divergence_count": len(divergences), "by_type": {kind: {"count": counts[kind]} for kind in TYPES if counts[kind]}, "amount_difference_abs_cents": amount_difference, "only_in_bank_gross_cents": only_bank, "only_in_csv_gross_cents": only_csv, "duplicate_group_count": counts["duplicate"], "manual_review_group_count": counts["manual_review"]}


def connect_readonly(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
