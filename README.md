# FinRecon AI

Servidor MCP que expõe ferramentas determinísticas de conciliação entre um banco SQLite (simulando a base interna) e CSVs de bandeira. O LLM orquestra e explica; toda comparação numérica roda em código testado, com valores em centavos inteiros.

## Princípio de design

O LLM nunca compara nem soma valores. Ele interpreta o pedido, chama as ferramentas, resume o resultado e sugere hipóteses. Cada divergência traz os dois lados (IDs e valores), então qualquer resultado é verificável sem confiar no modelo.

## Como rodar

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m conciliacao.generator
python -m conciliacao.evaluate
pytest
python -m conciliacao.server
```

Cliente MCP (exemplo para Claude Desktop):

```json
{
  "mcpServers": {
    "finrecon-ai": {
      "command": "/caminho/.venv/bin/python",
      "args": ["-m", "conciliacao.server"],
      "cwd": "/caminho/conciliacao-mcp"
    }
  }
}
```

Pedido de exemplo: "Concilie o arquivo bandeira.csv do dia 2026-10-19 com o banco e gere o relatório."

## Ferramentas

| Ferramenta | O que faz |
|---|---|
| `listar_schema` | Tabelas, colunas e CSVs disponíveis |
| `consultar_transacoes` | Consulta parametrizada somente leitura, com limite de linhas |
| `ler_csv` | Carrega e normaliza o CSV (valor, fuso, status) |
| `conciliar` | Executa o matching e classifica as divergências |
| `detalhar_divergencia` | Evidência completa de uma divergência, identificada por `finding_id` |
| `gerar_relatorio` | Relatório em Markdown de uma execução |

## Matching

1. Match exato por NSU somente quando a referência é única nos dois lados. Qualquer repetição vira um grupo `duplicate` para investigação, sem pareamento arbitrário.
2. Para linhas do CSV sem NSU, candidatos usam estabelecimento normalizado, final do cartão e janela de 24h; valor é comparado depois do pareamento.
3. Mais de um candidato composto vira `manual_review`, nunca um palpite.
4. Pares encontrados são comparados em valor (centavos), status e horário. O dia de negócio é America/Sao_Paulo e timestamps precisam incluir fuso.

Tipos: `value_mismatch`, `only_in_bank`, `only_in_csv`, `duplicate`, `date_mismatch`, `status_mismatch`, `manual_review`.

## Segurança e auditoria

- Banco aberto com `mode=ro`; não existe ferramenta de escrita.
- Nenhum SQL livre vindo do LLM: só consultas parametrizadas.
- CSV só é lido diretamente dentro de `data/`, com extensão `.csv`; caminhos relativos, subpastas e links simbólicos são bloqueados.
- Cada CSV é limitado a 10 MiB e 100.000 linhas. Pré-visualizações e consultas têm limite explícito de 500 registros.
- Cada chamada gera uma linha em `logs/audit.jsonl` (parâmetros, status, duração).
- `execucao_id` usa o hash SHA-256 completo do CSV, registros do banco, parâmetros e versão das regras; a execução salva metadados de reprodução em `runs/`.

## Contrato do CSV

O CSV usa UTF-8 (BOM aceito), delimitador `;` e exatamente as colunas listadas em `listar_schema`. O arquivo inteiro é rejeitado quando houver cabeçalho inválido, coluna duplicada ou ausente, campo obrigatório vazio, valor/status inválido, cartão diferente de quatro dígitos ou data sem fuso. A mensagem lista os erros por linha; dados válidos não são processados parcialmente.

## Avaliação

`generator` cria 2.000 transações, injeta 10 divergências de cada tipo (60 no total) e grava o gabarito com um identificador por achado. Também cria 10 linhas do CSV sem NSU que devem ser conciliadas pela chave composta sem gerar alerta. `evaluate` mede precisão e recall por tipo; quando não houver previsões ou exemplos, a métrica é indefinida (`null`), e não 100%.

Resultado atual no dataset sintético (seed 7): precisão 100% e recall 100%. O dataset é sintético e as regras são as mesmas que geraram os erros, então isso valida a implementação e não prova desempenho em dados reais.

## Limitações conhecidas

- Dados sintéticos; sem ruído real de bandeira (layouts diferentes, estornos parciais, parcelamento).
- Matching composto ainda exige estabelecimento, cartão e proximidade temporal inequívocos; casos ambíguos exigem revisão manual.
- Carrega o dia inteiro em memória; para volumes grandes seria preciso processar em lotes.

## Próximos passos

- Cliente do agente (LLM) chamando as ferramentas, e experimento A (LLM compara valores) vs. B (ferramentas determinísticas) para medir erros.
- Baseline manual cronometrado (SQL + planilha) para o slide de resultado.
- Casos mais difíceis no gerador (estorno parcial, parcelas, arredondamento).
