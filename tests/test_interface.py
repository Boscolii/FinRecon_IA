import subprocess
import threading

import pytest

from conciliacao import interface


def test_interface_encaminha_pergunta_e_pasta_sem_shell(tmp_path, monkeypatch):
    (tmp_path / "banco.db").touch()
    (tmp_path / "bandeira.csv").touch()
    chamadas = []

    def processo(*args, **kwargs):
        chamadas.append((args, kwargs))
        class Processo:
            returncode = 0
            def communicate(self, timeout=None):
                return "Resultado da conciliação", ""
        return Processo()

    monkeypatch.setattr(interface.subprocess, "Popen", processo)
    sucesso, resposta = interface.executar_agente("Concilie o arquivo", tmp_path)
    assert sucesso and resposta == "Resultado da conciliação"
    args, kwargs = chamadas[0]
    assert args[0][-1] == "Concilie o arquivo"
    assert "--sem-metricas" in args[0]
    assert "--listar-todas" in args[0]
    assert kwargs["env"]["CONCILIACAO_DB"] == str(tmp_path / "banco.db")
    assert kwargs["env"]["CONCILIACAO_DATA_DIR"] == str(tmp_path)
    assert not kwargs.get("shell", False)


def test_interface_rejeita_pasta_invalida_e_pergunta_vazia(tmp_path):
    with pytest.raises(ValueError, match="pergunta"):
        interface.executar_agente("  ", tmp_path)
    with pytest.raises(ValueError, match="banco.db"):
        interface.executar_agente("Concilie", tmp_path)


def test_interface_exibe_erro_do_agente(tmp_path, monkeypatch):
    (tmp_path / "banco.db").touch()
    (tmp_path / "bandeira.csv").touch()
    class Processo:
        returncode = 1
        def communicate(self, timeout=None):
            return "", "Chave não configurada"
    monkeypatch.setattr(interface.subprocess, "Popen", lambda *a, **kw: Processo())
    sucesso, mensagem = interface.executar_agente("Concilie", tmp_path)
    assert not sucesso and "Chave não configurada" in mensagem


@pytest.mark.parametrize("cancelar", [True, False])
def test_interface_encerra_processo_ao_cancelar_ou_exceder_prazo(tmp_path, monkeypatch, cancelar):
    (tmp_path / "banco.db").touch()
    (tmp_path / "bandeira.csv").touch()
    class Processo:
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("agent", timeout)
    processo = Processo()
    encerrados = []
    monkeypatch.setattr(interface.subprocess, "Popen", lambda *a, **kw: processo)
    monkeypatch.setattr(interface, "_encerrar_processo", encerrados.append)
    monkeypatch.setattr(interface, "TEMPO_LIMITE_SEGUNDOS", 0 if not cancelar else 300)
    evento = threading.Event()
    if cancelar:
        evento.set()
    sucesso, mensagem = interface.executar_agente("Concilie", tmp_path, evento)
    assert not sucesso and encerrados == [processo]
    assert "cancelada" in mensagem if cancelar else "tempo limite" in mensagem
