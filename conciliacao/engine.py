import csv
import hashlib
import io
import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

STATUS_MAP = {"APROVADA": "approved", "ESTORNADA": "reversed", "APPROVED": "approved", "REVERSED": "reversed"}
COMPOSITE_WINDOW = timedelta(hours=24)
BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")
CSV_HEADER = ("nsu", "estabelecimento", "valor", "data_hora", "situacao", "final_cartao")
TYPES = ("value_mismatch", "only_in_bank", "only_in_csv", "duplicate", "date_mismatch", "status_mismatch", "manual_review")
MAX_TOL_CENTS, MAX_TOL_MINUTES = 1_000_000, 1_440
MIN_AMOUNT_CENTS, MAX_AMOUNT_CENTS = -(2**63), 2**63 - 1


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
    if not re.fullmatch(r"[+-]?(?:(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}|\d+(?:\.\d{1,2})?)", text):
        raise ValueError(f"formato de valor invalido: {raw}")
    negative = text.startswith("-")
    text = text.lstrip("+-")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    whole, _, fraction = text.partition(".")
    digits = (whole + fraction.ljust(2, "0")).lstrip("0") or "0"
    # O SQLite armazena inteiros de até 64 bits; rejeite valores fora desse
    # intervalo antes de converter strings muito grandes em inteiro.
    if len(digits) > 19:
        raise ValueError("valor fora do intervalo suportado em centavos")
    cents = int(digits) * (-1 if negative else 1)
    if not MIN_AMOUNT_CENTS <= cents <= MAX_AMOUNT_CENTS:
        raise ValueError("valor fora do intervalo suportado em centavos")
    return cents


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
    rows = conn.execute("SELECT id, nsu, merchant, amount_cents, occurred_at, status, card_last4 FROM transactions ORDER BY id")
    selected = []
    for row_id, nsu, merchant, amount, raw_time, status, last4 in rows:
        occurred_at = parse_dt(raw_time)
        if start <= occurred_at < end:
            if not isinstance(amount, int) or isinstance(amount, bool):
                raise ValueError(f"transacao do banco id {row_id}: amount_cents deve ser um inteiro em centavos")
            selected.append(Tx("bank", "" if nsu is None else str(nsu).strip(), norm_merchant(merchant), amount, occurred_at, norm_status(status), last4))
    return selected


def load_csv(path, start=None, end=None):
    errors, out = [], []
    stream = io.StringIO(path.decode("utf-8-sig"), newline="") if isinstance(path, bytes) else Path(path).open(newline="", encoding="utf-8-sig")
    with stream as fh:
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
                if None in row or None in row.values():
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
    identity = []
    if bank.merchant != other.merchant:
        identity.append(f"estabelecimento: banco {bank.merchant}, csv {other.merchant}")
    if bank.card_last4 != other.card_last4:
        identity.append(f"final do cartao: banco {bank.card_last4}, csv {other.card_last4}")
    if identity:
        found.append(_divergence("manual_review", bank.nsu, [bank], [other], detail="Dados divergentes para o mesmo NSU; revisao manual necessaria: " + "; ".join(identity)))
    if abs(diff) > tol_cents:
        found.append(_divergence("value_mismatch", bank.nsu, [bank], [other], diff, f"banco {bank.amount_cents} centavos, csv {other.amount_cents} centavos"))
    if bank.status != other.status:
        found.append(_divergence("status_mismatch", bank.nsu, [bank], [other], detail=f"banco {bank.status}, csv {other.status}"))
    delta = abs(other.occurred_at - bank.occurred_at)
    if delta > timedelta(minutes=tol_minutes):
        found.append(_divergence("date_mismatch", bank.nsu, [bank], [other], detail=f"diferenca de {int(delta.total_seconds() // 60)} minutos"))
    return found


def _composite_components(bank, csv_rows):
    """Conecta candidatos em intervalos de horario sem materializar cada aresta."""
    bank_count = len(bank)
    parents = list(range(bank_count + len(csv_rows)))
    sizes = [1] * len(parents)

    def find(node):
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def union(a, b):
        a, b = find(a), find(b)
        if a == b:
            return
        if sizes[a] < sizes[b]:
            a, b = b, a
        parents[b] = a
        sizes[a] += sizes[b]

    grouped_bank = _group(range(bank_count), lambda i: (bank[i].merchant, bank[i].card_last4))
    grouped_csv = _group(range(len(csv_rows)), lambda i: (csv_rows[i].merchant, csv_rows[i].card_last4))
    degrees = [0] * len(csv_rows)
    for key, csv_indexes in grouped_csv.items():
        bank_indexes = sorted(grouped_bank.get(key, []), key=lambda i: bank[i].occurred_at)
        times = [bank[i].occurred_at for i in bank_indexes]
        # Cada fronteira entre registros consecutivos e unida no maximo uma vez,
        # mesmo quando milhares de intervalos se sobrepoem.
        next_boundary = list(range(len(bank_indexes)))

        def next_unjoined(i):
            while next_boundary[i] != i:
                next_boundary[i] = next_boundary[next_boundary[i]]
                i = next_boundary[i]
            return i

        for ci in csv_indexes:
            timestamp = csv_rows[ci].occurred_at
            left = bisect_left(times, timestamp - COMPOSITE_WINDOW)
            right = bisect_right(times, timestamp + COMPOSITE_WINDOW)
            degrees[ci] = right - left
            if left == right:
                continue
            union(bank_indexes[left], bank_count + ci)
            boundary = next_unjoined(left)
            while boundary < right - 1:
                union(bank_indexes[boundary], bank_indexes[boundary + 1])
                next_boundary[boundary] = next_unjoined(boundary + 1)
                boundary = next_unjoined(boundary)

    components = defaultdict(lambda: {"bank": [], "csv": [], "candidate_count": 0})
    for bi in range(bank_count):
        components[find(bi)]["bank"].append(bi)
    for ci in range(len(csv_rows)):
        group = components[find(bank_count + ci)]
        group["csv"].append(ci)
        group["candidate_count"] += degrees[ci]
    return components.values()


def reconcile(bank, csv_rows, tol_cents=0, tol_minutes=5, target_window=None):
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
    bank_candidates = duplicate_bank + bank_left
    unmatched_bank, unmatched_csv = [], list(csv_unmatched_id)
    for component in _composite_components(bank_candidates, csv_left):
        bank_indexes, csv_indexes = component["bank"], component["csv"]
        b_rows = [bank_candidates[i] for i in bank_indexes]
        c_rows = [csv_left[i] for i in csv_indexes]
        has_duplicate = any(i < len(duplicate_bank) for i in bank_indexes)
        if not c_rows:
            unmatched_bank.extend(bank_candidates[i] for i in bank_indexes if i >= len(duplicate_bank))
        elif not b_rows:
            unmatched_csv.extend(c_rows)
        elif has_duplicate:
            divergences.append(_divergence("manual_review", "", b_rows, c_rows,
                detail="Candidatos compostos envolvidos em grupo duplicado; revisao manual necessaria"))
        elif len(b_rows) == len(c_rows) == 1:
            pairs.append((b_rows[0], c_rows[0]))
        else:
            count = component["candidate_count"]
            divergences.append(_divergence("manual_review", "", b_rows, c_rows, detail=f"{count} candidato(s) composto(s) para estabelecimento/cartao; nenhum pareamento automatico foi feito"))
    for tx in unmatched_bank:
        divergences.append(_divergence("only_in_bank", tx.nsu, [tx], detail="transacao ausente no csv"))
    for tx in unmatched_csv:
        divergences.append(_divergence("only_in_csv", tx.nsu, csv=[tx], detail="transacao ausente no banco"))
    clean = 0
    def in_target(tx):
        return target_window is None or target_window[0] <= tx.occurred_at < target_window[1]
    matched = 0
    for b_tx, c_tx in pairs:
        if not (in_target(b_tx) or in_target(c_tx)):
            continue
        matched += 1
        found = _compare(b_tx, c_tx, tol_cents, tol_minutes)
        divergences.extend(found)
        clean += not found
    if target_window is not None:
        def evidence_in_target(finding):
            for value in (finding.bank, finding.csv):
                for row in value if isinstance(value, list) else ([value] if value else []):
                    if target_window[0] <= parse_dt(row["occurred_at"]) < target_window[1]:
                        return True
            return False
        divergences = [finding for finding in divergences if evidence_in_target(finding)]
    divergences.sort(key=lambda d: (d.type, d.nsu, d.finding_id))
    counts = Counter(d.finding_id for d in divergences)
    occurrences = defaultdict(int)
    for i, finding in enumerate(divergences):
        if counts[finding.finding_id] > 1:
            occurrences[finding.finding_id] += 1
            divergences[i] = replace(finding, finding_id=f"{finding.finding_id}:ocorrencia:{occurrences[finding.finding_id]}")
    return {"summary": summarize([tx for tx in bank if in_target(tx)], [tx for tx in csv_rows if in_target(tx)], divergences, matched, clean), "divergences": divergences}


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
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
