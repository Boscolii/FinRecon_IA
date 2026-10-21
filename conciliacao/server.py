import functools
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import engine

DB_PATH = Path(os.environ.get("CONCILIACAO_DB", "data/banco.db"))
DATA_DIR = Path(os.environ.get("CONCILIACAO_DATA_DIR", "data"))
LOG_PATH = Path(os.environ.get("CONCILIACAO_LOG", "logs/audit.jsonl"))
RUNS_DIR = Path(os.environ.get("CONCILIACAO_RUNS", "runs"))
MAX_ROWS = 500
PREVIEW_DIVERGENCES = 25
MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_CSV_LINES = 100_000
MAX_REPORT_ITEMS = 50
RULES_VERSION = "2026-10-21.2"
RUN_FORMAT_VERSION = 2

mcp = FastMCP("FinRecon AI")


def _log(tool, params, status, detail=""):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "tool": tool, "params": params, "status": status, "detail": detail}
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def audited(fn):
    @functools.wraps(fn)
    def wrapper(**kwargs):
        started = time.perf_counter()
        try:
            out = fn(**kwargs)
        except Exception as exc:
            _log(fn.__name__, kwargs, "erro", str(exc))
            raise
        _log(fn.__name__, kwargs, "ok", f"{(time.perf_counter() - started) * 1000:.0f} ms")
        return out

    return wrapper


def _safe_csv(name):
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("arquivo csv nao permitido")
    requested = DATA_DIR / name
    data_dir = DATA_DIR.resolve()
    if requested.is_symlink():
        raise ValueError("arquivo csv nao permitido: links simbolicos nao sao aceitos")
    path = requested.resolve()
    if path.parent != data_dir or path.suffix.lower() != ".csv" or not path.is_file():
        raise ValueError(f"arquivo csv nao permitido ou inexistente: {name}")
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError(f"arquivo csv excede o tamanho maximo de {MAX_CSV_BYTES} bytes")
    with path.open("rb") as fh:
        line_count = sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1024 * 1024), b""))
    if line_count > MAX_CSV_LINES:
        raise ValueError(f"arquivo csv excede o limite de {MAX_CSV_LINES} linhas")
    return path


def _limit(value, name, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} deve ser inteiro entre 1 e {maximum}")
    return value


def _brl(cents):
    sign = "-" if cents < 0 else ""
    reais, centavos = divmod(abs(cents), 100)
    return f"{sign}R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def _run_path(run_id):
    if not isinstance(run_id, str) or len(run_id) != 64 or any(char not in "0123456789abcdef" for char in run_id):
        raise ValueError("execucao_id invalido")
    return RUNS_DIR / f"{run_id}.json"


def _load_run(run_id):
    path = _run_path(run_id)
    if not path.is_file():
        raise ValueError(f"execucao nao encontrada: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


@mcp.tool()
@audited
def listar_schema() -> dict:
    conn = engine.connect_readonly(DB_PATH)
    columns = {name: [{"coluna": c[1], "tipo": c[2]} for c in conn.execute(f"PRAGMA table_info({name})")] for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    conn.close()
    return {
        "banco": columns,
        "csv": {"delimitador": ";", "colunas": ["nsu", "estabelecimento", "valor (formato 1234,56)", "data_hora (ISO com fuso)", "situacao (APROVADA|ESTORNADA)", "final_cartao"]},
        "arquivos_csv": sorted(p.name for p in DATA_DIR.glob("*.csv")),
    }


@mcp.tool()
@audited
def consultar_transacoes(data_inicio: str, data_fim: str, limite: int = 50) -> dict:
    start, _ = engine.day_window(data_inicio)
    _, end = engine.day_window(data_fim)
    if start >= end:
        raise ValueError("data_inicio deve ser anterior ou igual a data_fim")
    limit = _limit(limite, "limite", MAX_ROWS)
    params = (start.isoformat(), end.isoformat())
    conn = engine.connect_readonly(DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents), 0) FROM transactions WHERE occurred_at >= ? AND occurred_at < ?", params).fetchone()
        rows = conn.execute("SELECT nsu, merchant, amount_cents, occurred_at, status, card_last4 FROM transactions WHERE occurred_at >= ? AND occurred_at < ? ORDER BY id LIMIT ?", (*params, limit)).fetchall()
    finally:
        conn.close()
    keys = ("nsu", "merchant", "amount_cents", "occurred_at", "status", "card_last4")
    return {"total": total[0], "soma_centavos": total[1], "retornadas": len(rows), "transacoes": [dict(zip(keys, r)) for r in rows]}


@mcp.tool()
@audited
def ler_csv(arquivo: str, limite: int = 20) -> dict:
    rows = engine.load_csv(_safe_csv(arquivo))
    limit = _limit(limite, "limite", MAX_ROWS)
    return {"total": len(rows), "soma_centavos": sum(r.amount_cents for r in rows), "retornadas": min(len(rows), limit), "truncado": len(rows) > limit, "amostra": [r.to_dict() for r in rows[:limit]]}


@mcp.tool()
@audited
def conciliar(arquivo_csv: str, data: str, tolerancia_centavos: int = 0, tolerancia_minutos: int = 5, margem_dias: int = 1) -> dict:
    path = _safe_csv(arquivo_csv)
    start, end = engine.day_window(data, margem_dias)
    conn = engine.connect_readonly(DB_PATH)
    try:
        bank = engine.load_bank(conn, start, end)
    finally:
        conn.close()
    csv_rows = engine.load_csv(path, start, end)
    result = engine.reconcile(bank, csv_rows, tolerancia_centavos, tolerancia_minutos)
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    digest.update(json.dumps({"rules_version": RULES_VERSION, "data": data, "tolerancia_centavos": tolerancia_centavos, "tolerancia_minutos": tolerancia_minutos, "margem_dias": margem_dias, "bank": [t.to_dict() for t in bank]}, sort_keys=True).encode())
    run_id = digest.hexdigest()
    run = {
        "execucao_id": run_id,
        "parametros": {"arquivo_csv": arquivo_csv, "data": data, "tolerancia_centavos": tolerancia_centavos, "tolerancia_minutos": tolerancia_minutos, "margem_dias": margem_dias},
        "metadata": {"run_format_version": RUN_FORMAT_VERSION, "rules_version": RULES_VERSION, "csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bank_records": len(bank)},
        "summary": result["summary"],
        "divergences": [d.to_dict() for d in result["divergences"]],
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _run_path(run_id).write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    preview = run["divergences"][:PREVIEW_DIVERGENCES]
    return {"execucao_id": run_id, "summary": run["summary"], "divergences": preview, "truncado": len(run["divergences"]) > len(preview)}


@mcp.tool()
@audited
def detalhar_divergencia(execucao_id: str, finding_id: str) -> dict:
    run = _load_run(execucao_id)
    matches = [d for d in run["divergences"] if d["finding_id"] == finding_id]
    if len(matches) != 1:
        raise ValueError("divergencia nao encontrada")
    return matches[0]


@mcp.tool()
@audited
def gerar_relatorio(execucao_id: str, max_por_tipo: int = 10) -> str:
    run = _load_run(execucao_id)
    max_por_tipo = _limit(max_por_tipo, "max_por_tipo", MAX_REPORT_ITEMS)
    s, p = run["summary"], run["parametros"]
    lines = [
        f"# Relatorio de conciliacao {execucao_id}",
        "",
        f"- Arquivo: {p['arquivo_csv']} | Data: {p['data']} (margem de {p['margem_dias']} dia)",
        f"- Tolerancias: {p['tolerancia_centavos']} centavos, {p['tolerancia_minutos']} minutos",
        f"- Banco: {s['bank_total']} | CSV: {s['csv_total']} | Pares conciliados: {s['matched_pairs']} ({s['clean_pairs']} sem divergencia)",
        f"- Divergencias: {s['divergence_count']} | Diferencas absolutas de valor em pares: {_brl(s['amount_difference_abs_cents'])}",
        f"- Ausentes no CSV (valor bruto): {_brl(s['only_in_bank_gross_cents'])} | Ausentes no banco (valor bruto): {_brl(s['only_in_csv_gross_cents'])}",
        f"- Grupos duplicados: {s['duplicate_group_count']} | Grupos em revisao manual: {s['manual_review_group_count']}",
        "",
        "| Tipo | Quantidade |",
        "|---|---|",
    ]
    lines += [f"| {t} | {v['count']} |" for t, v in s["by_type"].items()]
    for kind in s["by_type"]:
        items = [d for d in run["divergences"] if d["type"] == kind]
        lines += ["", f"## {kind} ({len(items)})", ""]
        for d in items[:max_por_tipo]:
            lines.append(f"- nsu {d['nsu'] or '(sem nsu)'}: {d['detail']} ({_brl(d['diff_cents'])})")
        if len(items) > max_por_tipo:
            lines.append(f"- ... e mais {len(items) - max_por_tipo}; use detalhar_divergencia")
    return "\n".join(lines)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
