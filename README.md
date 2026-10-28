# FinRecon AI

Servidor MCP que expõe ferramentas determinísticas de conciliação entre um banco SQLite (simulando a base interna) e CSVs de bandeira. O LLM orquestra e explica; toda comparação numérica roda em código testado, com valores em centavos inteiros.

## Princípio de design

O motor, e não o LLM, é responsável por comparar e somar valores. O agente é instruído a interpretar o pedido, chamar ferramentas e explicar os resultados; uma resposta de modelo pode errar e deve ser conferida com a evidência determinística. As divergências incluem os registros disponíveis de cada fonte e um identificador do achado; quando um registro está ausente, não há evidência do outro lado.

## Como rodar no Windows (PowerShell)

O uso principal é pela interface gráfica. O PowerShell é necessário apenas para preparar o ambiente e abrir a janela. Este procedimento foi verificado no Windows com Python 3.14.6.

### 1. Instale as dependências

Na pasta do projeto, crie o ambiente virtual e instale as dependências:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Configure a chave da API

Se ainda não existir um arquivo `.env` na pasta do projeto, copie o exemplo e abra-o para edição:

```powershell
Copy-Item .env.example .env
notepad .env
```

Se o `.env` já existir, abra-o diretamente com `notepad .env`; não substitua sua configuração atual. Para usar Gemini, preencha `GEMINI_API_KEY` e mantenha `LLM_PROVIDER=gemini`. Salve o arquivo e nunca compartilhe sua chave ou envie o `.env` ao GitHub. O uso da API está sujeito à disponibilidade, aos limites e à cobrança do provedor.

### 3. Prepare os dados sintéticos

Crie os dados em uma pasta **nova**:

```powershell
.\.venv\Scripts\python.exe -m conciliacao.generator --out dados-demo --seed 7
```

O gerador substitui `banco.db`, `bandeira.csv` e `gabarito.json` se já existirem no destino. Escolha uma pasta nova para preservar seus dados.

### 4. Abra a interface

```powershell
.\.venv\Scripts\python.exe -m conciliacao.interface
```

Na janela:

- Clique em **Escolher pasta** e selecione `dados-demo`, ou outra pasta com `banco.db` e o CSV sintético.
- Escreva sua pergunta, por exemplo: “Concilie bandeira.csv no dia 2026-10-19 e explique as divergências.”
- Clique em **Enviar pergunta** e aguarde a resposta.

A janela apresenta a explicação do agente seguida da lista completa de achados por categoria, com as evidências do motor. Se não conseguir recuperar a lista inteira, informa a falha. Cada pergunta inicia uma consulta independente; o Python precisa ter Tkinter instalado.

Para interromper uma consulta, clique em **Cancelar consulta** ou feche a janela. A consulta também é interrompida se ultrapassar cinco minutos.

## Agente de IA

O agente inicia o servidor MCP localmente, apresenta apenas as ferramentas permitidas ao modelo e devolve os resultados determinísticos para explicação. Ele não recebe acesso direto ao SQLite, ao sistema de arquivos ou a SQL livre.

O padrão é Gemini, com `GEMINI_API_KEY`, modelo `gemini-3.5-flash-lite` e `GEMINI_THINKING_LEVEL=minimal`, perfil rápido para perguntas operacionais; também são aceitos `openai`, com `OPENAI_API_KEY`, e endpoints que implementem OpenAI Chat Completions/tool calling, com `LLM_PROVIDER=openai_compatible`, `LLM_API_KEY` e `LLM_BASE_URL`. `FINRECON_MODEL` é opcional para Gemini e OpenAI, mas obrigatório para um endpoint compatível.

### Uso pelo terminal (opcional)

Para usar o agente diretamente pelo PowerShell, configure a pasta de dados na sessão e informe a pergunta:

```powershell
$env:CONCILIACAO_DATA_DIR = 'dados-demo'
$env:CONCILIACAO_DB = 'dados-demo/banco.db'
.\.venv\Scripts\python.exe -m conciliacao.agent "Concilie o arquivo bandeira.csv do dia 2026-10-19 e explique as divergencias."
```

Para executar os testes, use `.\.venv\Scripts\python.exe -m pytest`. Para iniciar somente o servidor MCP, use `.\.venv\Scripts\python.exe -m conciliacao.server`. O servidor se comunica via entrada e saída padrão e não é uma interface de chat.

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
4. Pares encontrados são comparados em valor (centavos), status e horário. Em pares pelo NSU, diferenças de estabelecimento ou final do cartão exigem revisão manual. O dia de negócio é America/Sao_Paulo e timestamps precisam incluir fuso.

Na busca composta, os horários são ordenados para localizar candidatos na janela de 24 horas. Grupos ambíguos são reunidos sem armazenar todas as combinações possíveis; os registros envolvidos continuam disponíveis como evidência para revisão.

Por padrão, o motor também busca candidatos no dia anterior e no seguinte. Os totais contam os registros do dia solicitado; achados e pares são incluídos quando pelo menos um registro pertence a esse dia. Um registro da margem pode aparecer como evidência de um achado ligado ao dia, mas não aumenta o total de registros daquele lado. A margem pode ser configurada na ferramenta `conciliar`.

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

Valores aceitos incluem `1234,56`, `1.234,56`, `1234.56` e números inteiros; separadores mal posicionados são rejeitados.
Os valores devem caber no intervalo inteiro de 64 bits em centavos do SQLite. No banco, `amount_cents` deve ser armazenado como inteiro; valores fracionários ou de outro tipo são rejeitados.

## Limitações conhecidas

- Dados sintéticos; sem ruído real de bandeira (layouts diferentes, estornos parciais, parcelamento).
- Matching composto ainda exige estabelecimento, cartão e proximidade temporal inequívocos; casos ambíguos exigem revisão manual.
- Para tratar timestamps com offsets diferentes corretamente, a leitura do banco examina todos os horários e mantém os registros selecionados em memória; volumes grandes exigem armazenamento UTC normalizado e consulta indexada ou processamento em lotes.
- Os registros e evidências de cada execução ainda são mantidos em memória e gravados em JSON; arquivos e grupos muito grandes exigem medição e podem precisar de armazenamento e apresentação em lotes.

## Próximos passos

- Repetir medições do agente e o teste manual sob condições comparáveis; medir também tokens e custo real.
- Adicionar identificadores individuais de origem e casos de avaliação independentes para dados sem NSU, ambiguidades e anomalias combinadas.
- Expandir regras financeiras apenas após definir requisitos, por exemplo para estornos parciais e parcelamento.
