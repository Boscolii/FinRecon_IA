from types import SimpleNamespace

import pytest

from conciliacao import interface


def test_interface_encaminha_pergunta_e_pasta_sem_shell(tmp_path, monkeypatch):
    (tmp_path / "banco.db").touch()
    (tmp_path / "bandeira.csv").touch()
    chamadas = []

    def processo(*args, **kwargs):
        chamadas.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="Resultado da conciliação", stderr="")

    monkeypatch.setattr(interface.subprocess, "run", processo)
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
    monkeypatch.setattr(interface.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="Chave não configurada"))
    sucesso, mensagem = interface.executar_agente("Concilie", tmp_path)
    assert not sucesso and "Chave não configurada" in mensagem
