# FinRecon AI

Servidor MCP que expõe ferramentas determinísticas de conciliação entre um banco SQLite (simulando a base interna) e CSVs de bandeira. O LLM orquestra e explica; toda comparação numérica roda em código testado, com valores em centavos inteiros.

## Princípio de design

O motor, e não o LLM, é responsável por comparar e somar valores. O agente é instruído a interpretar o pedido, chamar ferramentas e explicar os resultados; uma resposta de modelo pode errar e deve ser conferida com a evidência determinística. As divergências incluem os registros disponíveis de cada fonte e um identificador do achado; quando um registro está ausente, não há evidência do outro lado.

## Como rodar no Windows (PowerShell)

Na pasta do projeto, crie o ambiente virtual e instale as dependências. Este procedimento foi verificado no Windows com Python 3.14.6:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```

Crie dados exclusivamente sintéticos em uma pasta **nova** e avalie o motor:

```powershell
.\.venv\Scripts\python.exe -m conciliacao.generator --out dados-demo --seed 7
.\.venv\Scripts\python.exe -m conciliacao.evaluate --data dados-demo --day 2026-10-19
```

O gerador substitui `banco.db`, `bandeira.csv` e `gabarito.json` se já existirem no destino. Escolha uma pasta nova para preservar seus dados. Para executar o agente nessa pasta, configure-a no mesmo terminal:

```powershell
$env:CONCILIACAO_DATA_DIR = 'dados-demo'
$env:CONCILIACAO_DB = 'dados-demo/banco.db'
```

Para iniciar somente o servidor MCP, use `.\.venv\Scripts\python.exe -m conciliacao.server`. Ele se comunica pelo protocolo MCP via entrada e saída padrão; não é uma interface de chat no terminal.

## Agente de IA

### Janela simples

Com as dependências instaladas e a chave configurada no `.env`, abra a interface local no Windows:

```powershell
.\.venv\Scripts\python.exe -m conciliacao.interface
```

Selecione a pasta que contém `banco.db` e o CSV sintético, escreva sua pergunta e clique em **Enviar pergunta**. Quando a consulta executar uma conciliação, a janela apresenta a explicação do agente seguida da lista completa de achados, agrupados por categoria, com as evidências retornadas pelo motor. Se a listagem não puder ser recuperada integralmente, a janela informa a falha. As métricas de tempo e tokens permanecem disponíveis no uso pelo terminal. A interface exige que o banco se chame `banco.db`; cada pergunta é uma consulta independente. O Python precisa ter Tkinter instalado.

### Uso pelo terminal

O agente é um cliente de linha de comando que inicia o servidor MCP localmente, apresenta apenas as ferramentas permitidas ao modelo e devolve os resultados determinísticos para explicação. Ele não recebe acesso direto ao SQLite, ao sistema de arquivos ou a SQL livre.

Crie um arquivo `.env` a partir de `.env.example` e configure o provedor. O padrão é Gemini, com `GEMINI_API_KEY`, modelo `gemini-3.5-flash-lite` e `GEMINI_THINKING_LEVEL=minimal`, perfil rápido para perguntas operacionais; também são aceitos `openai`, com `OPENAI_API_KEY`, e qualquer endpoint que implemente o padrão OpenAI Chat Completions/tool calling, por meio de `LLM_PROVIDER=openai_compatible`, `LLM_API_KEY` e `LLM_BASE_URL`. Nunca versione uma chave real. `FINRECON_MODEL` é opcional para Gemini e OpenAI, mas obrigatório para um endpoint compatível.

No PowerShell:

```powershell
$env:GEMINI_API_KEY = "sua_chave"
.\.venv\Scripts\python.exe -m conciliacao.agent "Concilie o arquivo bandeira.csv do dia 2026-10-19 e explique as divergencias."
```

O agente pode chamar somente `listar_schema`, `consultar_transacoes`, `ler_csv`, `conciliar`, `listar_divergencias`, `detalhar_divergencia` e `gerar_relatorio`. O limite é oito rodadas de ferramentas por pergunta. Em caso de erro de ferramenta, ele recebe a falha estruturada e deve informar a limitação, sem inventar um resultado. Um provedor que não ofereça tool calling precisa de um adaptador próprio e não deve ser usado para a demonstração do agente.

Ao finalizar, o agente mostra o uso de tokens informado pelo provedor e uma estimativa hipotética para o Gemini configurado, quando disponível. Veja [a análise de custos](docs/analise-custos.md) para preços, medições e limitações.

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
| `listar_divergencias` | Lista os achados de uma execução em páginas de até 100, com evidências |
| `detalhar_divergencia` | Evidência completa de uma divergência, identificada por `finding_id` |
| `gerar_relatorio` | Relatório em Markdown de uma execução |

## Matching

1. Match exato por NSU somente quando a referência é única nos dois lados. Qualquer repetição, mesmo presente em apenas uma fonte, vira um grupo `duplicate` para investigação, sem pareamento arbitrário. Um candidato do CSV sem NSU ligado a um grupo duplicado do banco também vai para revisão manual.
2. Para linhas do CSV sem NSU, candidatos usam estabelecimento normalizado, final do cartão e janela de 24h; valor é comparado depois do pareamento.
3. Mais de um candidato composto vira `manual_review`, nunca um palpite.
4. Pares encontrados são comparados em valor (centavos), status e horário. O dia de negócio é America/Sao_Paulo e timestamps precisam incluir fuso.

Tipos: `value_mismatch`, `only_in_bank`, `only_in_csv`, `duplicate`, `date_mismatch`, `status_mismatch`, `manual_review`.

## Segurança e auditoria

- Banco aberto com `mode=ro`; não existe ferramenta de escrita.
- Nenhum SQL livre vindo do LLM: só consultas parametrizadas.
- CSV só é lido diretamente dentro da pasta de dados configurada (`data/` por padrão), com extensão `.csv`; travessia de diretórios, subpastas e links simbólicos são bloqueados.
- Cada CSV é limitado a 10 MiB e 100.000 linhas. Pré-visualizações e consultas têm limite explícito de 500 registros.
- Cada chamada gera uma linha em `logs/audit.jsonl` (parâmetros, status, duração).
- `execucao_id` usa o hash SHA-256 completo do CSV, registros do banco, parâmetros e versão das regras; a execução salva metadados de reprodução em `runs/`.

## Contrato do CSV

O CSV usa UTF-8 (BOM aceito), delimitador `;` e exatamente as colunas listadas em `listar_schema`. O arquivo inteiro é rejeitado quando houver cabeçalho inválido, coluna duplicada ou ausente, campo obrigatório vazio, valor/status inválido, cartão diferente de quatro dígitos ou data sem fuso. A mensagem lista os erros por linha; dados válidos não são processados parcialmente.

## Avaliação

Consulte [o roteiro de demonstração e testes](docs/roteiro-demonstracao.md) para reproduzir os dados sintéticos, executar a avaliação e entender suas limitações.
Para preparar sua explicação, consulte também o [guia para a entrevista](docs/guia-entrevista.md).

`generator` cria 2.000 transações, injeta 10 divergências de cada tipo (60 no total) e grava no gabarito as evidências esperadas de cada fonte. Também cria 10 linhas do CSV sem NSU que devem ser conciliadas pela chave composta sem gerar alerta. `evaluate` compara o tipo e as evidências, preservando a multiplicidade, e mede precisão e recall por tipo; quando não houver previsões ou exemplos, a métrica é indefinida (`null`), e não 100%. Gabaritos anteriores, sem evidências, precisam ser gerados novamente em uma pasta separada.

Resultado atual no dataset sintético (seed 7): precisão 100% e recall 100%. O dataset é sintético e as regras são as mesmas que geraram os erros, então isso valida a implementação e não prova desempenho em dados reais.

## Limitações conhecidas

- Dados sintéticos; sem ruído real de bandeira (layouts diferentes, estornos parciais, parcelamento).
- Matching composto ainda exige estabelecimento, cartão e proximidade temporal inequívocos; casos ambíguos exigem revisão manual.
- Carrega o dia inteiro em memória; para volumes grandes seria preciso processar em lotes.

## Próximos passos

- Repetir medições do agente e o teste manual sob condições comparáveis; medir também tokens e custo real.
- Adicionar identificadores individuais de origem e casos de avaliação independentes para dados sem NSU, ambiguidades e anomalias combinadas.
- Expandir regras financeiras apenas após definir requisitos, por exemplo para estornos parciais e parcelamento.
