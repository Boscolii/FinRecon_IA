"""Interface local simples para executar o agente existente."""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk


PROJECT_DIR = Path(__file__).resolve().parent.parent
EXAMPLE = "Concilie bandeira.csv no dia 2026-10-19 e explique as divergências."
TEMPO_LIMITE_SEGUNDOS = 300


def _encerrar_processo(processo):
    """Encerra o agente e seus filhos, incluindo o servidor MCP."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(processo.pid), "/T", "/F"],
                           capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(processo.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if processo.poll() is None:
        processo.kill()
    processo.communicate()


def executar_agente(pergunta, pasta_dados, cancelamento=None):
    """Executa o CLI sem shell e retorna a saída ou uma mensagem de erro."""
    pasta = Path(pasta_dados).expanduser().resolve()
    if not pergunta.strip():
        raise ValueError("Escreva uma pergunta antes de enviar.")
    if not pasta.is_dir() or not (pasta / "banco.db").is_file():
        raise ValueError("Selecione uma pasta de dados que contenha o arquivo banco.db.")
    if not any(pasta.glob("*.csv")):
        raise ValueError("A pasta selecionada precisa conter pelo menos um arquivo .csv.")

    ambiente = os.environ.copy()
    ambiente.update({
        "CONCILIACAO_DATA_DIR": str(pasta),
        "CONCILIACAO_DB": str(pasta / "banco.db"),
        "PYTHONIOENCODING": "utf-8",
    })
    opcoes_grupo = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == "nt" else {"start_new_session": True})
    processo = subprocess.Popen(
        [sys.executable, "-m", "conciliacao.agent", "--sem-metricas", "--listar-todas", pergunta.strip()],
        cwd=PROJECT_DIR,
        env=ambiente,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **opcoes_grupo,
    )
    prazo = time.monotonic() + TEMPO_LIMITE_SEGUNDOS
    while True:
        if cancelamento is not None and cancelamento.is_set():
            _encerrar_processo(processo)
            return False, "Consulta cancelada."
        if time.monotonic() >= prazo:
            _encerrar_processo(processo)
            return False, "A consulta ultrapassou o tempo limite de 5 minutos. Tente novamente."
        try:
            saida, erro = processo.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            continue
    if processo.returncode:
        return False, erro.strip() or "Não foi possível executar o agente."
    return True, saida.strip() or "O agente não retornou uma resposta."


class Interface:
    def __init__(self, janela):
        self.janela = janela
        self.resultados = queue.Queue()
        self.em_execucao = False
        self.cancelamento = threading.Event()
        self.fechando = False
        janela.title("FinRecon AI")
        janela.geometry("850x650")
        janela.minsize(600, 450)
        janela.protocol("WM_DELETE_WINDOW", self.fechar)

        quadro = ttk.Frame(janela, padding=16)
        quadro.pack(fill="both", expand=True)
        ttk.Label(quadro, text="FinRecon AI", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(quadro, text="Conciliação de dados sintéticos com explicações do agente.").pack(anchor="w", pady=(2, 14))

        ttk.Label(quadro, text="Pasta de dados (banco.db e CSV)").pack(anchor="w")
        linha_pasta = ttk.Frame(quadro)
        linha_pasta.pack(fill="x", pady=(4, 12))
        padrao = os.environ.get("CONCILIACAO_DATA_DIR", str(PROJECT_DIR / "data"))
        self.pasta = tk.StringVar(value=padrao)
        ttk.Entry(linha_pasta, textvariable=self.pasta).pack(side="left", fill="x", expand=True)
        ttk.Button(linha_pasta, text="Escolher pasta", command=self.escolher_pasta).pack(side="left", padx=(8, 0))

        ttk.Label(quadro, text="O que você gostaria de saber?").pack(anchor="w")
        self.pergunta = tk.Text(quadro, height=3, wrap="word", font=("Segoe UI", 10))
        self.pergunta.insert("1.0", EXAMPLE)
        self.pergunta.pack(fill="x", pady=(4, 8))
        linha_acao = ttk.Frame(quadro)
        linha_acao.pack(fill="x")
        self.botao = ttk.Button(linha_acao, text="Enviar pergunta", command=self.enviar)
        self.botao.pack(side="left")
        self.botao_cancelar = ttk.Button(linha_acao, text="Cancelar consulta", command=self.cancelar, state="disabled")
        self.botao_cancelar.pack(side="left", padx=(8, 0))
        self.estado = tk.StringVar(value="Pronto. Cada pergunta inicia uma nova consulta.")
        ttk.Label(linha_acao, textvariable=self.estado).pack(side="left", padx=12)

        ttk.Label(quadro, text="Resposta").pack(anchor="w", pady=(16, 4))
        self.resposta = scrolledtext.ScrolledText(quadro, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.resposta.pack(fill="both", expand=True)
        ttk.Label(quadro, text="Confira as evidências. A resposta da IA pode conter erros.").pack(anchor="w", pady=(8, 0))
        janela.after(100, self.verificar_resultados)

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(title="Selecione a pasta com banco.db e CSV")
        if pasta:
            self.pasta.set(pasta)

    def exibir(self, texto):
        self.resposta.configure(state="normal")
        self.resposta.delete("1.0", "end")
        self.resposta.insert("1.0", texto)
        self.resposta.configure(state="disabled")

    def enviar(self):
        if self.em_execucao:
            return
        pergunta = self.pergunta.get("1.0", "end").strip()
        pasta = self.pasta.get().strip()
        self.em_execucao = True
        self.cancelamento.clear()
        self.botao.configure(state="disabled")
        self.botao_cancelar.configure(state="normal")
        self.estado.set("Consultando... Aguarde a resposta.")
        self.exibir("Consultando o agente e as ferramentas MCP...")

        def executar():
            try:
                self.resultados.put(executar_agente(pergunta, pasta, self.cancelamento))
            except Exception as exc:
                self.resultados.put((False, str(exc)))

        threading.Thread(target=executar, daemon=True).start()

    def verificar_resultados(self):
        try:
            sucesso, texto = self.resultados.get_nowait()
        except queue.Empty:
            pass
        else:
            if self.fechando:
                self.janela.destroy()
                return
            self.exibir(texto)
            self.estado.set("Consulta concluída." if sucesso else "Não foi possível concluir a consulta.")
            self.em_execucao = False
            self.botao.configure(state="normal")
            self.botao_cancelar.configure(state="disabled")
        self.janela.after(100, self.verificar_resultados)

    def cancelar(self):
        if self.em_execucao:
            self.cancelamento.set()
            self.botao_cancelar.configure(state="disabled")
            self.estado.set("Cancelando consulta...")

    def fechar(self):
        if self.em_execucao:
            self.fechando = True
            self.cancelar()
            return
        self.janela.destroy()


def main():
    janela = tk.Tk()
    Interface(janela)
    janela.mainloop()


if __name__ == "__main__":
    main()
