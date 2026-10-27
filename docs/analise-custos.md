# FinRecon AI — consumo de tokens e estimativa de custos

## O que é medido

O agente soma os metadados de uso retornados em cada chamada ao modelo durante uma pergunta. Para Gemini, exibe tokens de entrada, saída, raciocínio e cache (o cache já está incluído na entrada), além do total informado pelo provedor. Para provedores compatíveis com OpenAI, a saída inclui os tokens de conclusão, inclusive eventual raciocínio já contabilizado pelo provedor; o custo não é estimado automaticamente.

Se faltar metadado em alguma chamada, o total mostrado é parcial e nenhuma estimativa é apresentada. Execuções anteriores à instrumentação não tiveram tokens registrados. Uma falha que encerre a execução pode ter consumido tokens, mas seu uso não necessariamente aparece na saída.

## Medições observadas

Em 27/10/2026, usando o Gemini `gemini-3.5-flash-lite`, nível de raciocínio `minimal`, data de negócio 19/10/2026 e o pedido “Concilie bandeira.csv no dia 2026-10-19 e informe as divergencias por tipo”:

| Conjunto sintético | Chamadas ao modelo | Chamadas MCP | Entrada | Saída | Raciocínio | Total | Estimativa paga padrão |
|---|---:|---:|---:|---:|---:|---:|---:|
| Amostra manual: 7 registros no banco e 5 no CSV | 2 | 1 | 2.907 | 511 | 0 | 3.418 | US$ 0,00214960 |
| Benchmark: 2.005 registros em cada fonte | 2 | 1 | 9.029 | 389 | 0 | 9.418 | US$ 0,00368120 |

As duas respostas identificaram, respectivamente, 5 e 60 achados. Foi feita uma medição de cada cenário; não é uma média. O servidor devolve uma prévia limitada de divergências, portanto o custo não cresce necessariamente na mesma proporção do número de registros. Pedidos diferentes, mais detalhes ou mais chamadas ao modelo podem alterar bastante o consumo.

## Como calcular

Referência consultada em 27/10/2026: [preços da Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing), modelo `gemini-3.5-flash-lite`, modalidade texto e plano pago Standard: US$ 0,30 por milhão de tokens de entrada e US$ 2,50 por milhão de tokens de saída, incluindo raciocínio. A API informa os campos em [UsageMetadata](https://ai.google.dev/api/generate-content).

```text
custo estimado em USD = (entrada × 0,30 + (saída + raciocínio) × 2,50) / 1.000.000
```

Na execução maior: `(9.029 × 0,30 + 389 × 2,50) / 1.000.000 = US$ 0,00368120`. Não havia tokens de cache nessa medição. O programa aplica a tarifa de entrada integral mesmo quando houver cache; essa é uma aproximação sem desconto e não substitui a fatura. O nível gratuito pode resultar em cobrança zero, conforme disponibilidade e limites da conta. Tarifas mudam; confira a página do provedor antes de usar o valor em uma decisão.

Se cada uma das próximas execuções consumisse exatamente o mesmo que a medição maior, a projeção hipotética seria US$ 3,6812 para 1.000 consultas ou US$ 36,812 para 10.000 consultas, apenas de tokens. É uma extrapolação, não previsão de fatura ou média observada.

## Avaliar viabilidade

1. Repetir consultas representativas com tamanhos, tipos de divergência e pedidos diferentes; registrar tokens, modelo, data e falhas.
2. Verificar no painel do provedor o plano, cobranças efetivas, limites e eventuais créditos gratuitos.
3. Estimar volume mensal e incluir custos de infraestrutura, desenvolvimento, operação e revisão manual, além de impostos e câmbio quando aplicáveis.
4. Medir a qualidade das respostas, porque menor custo não compensa uma explicação incorreta ou incompleta.
5. Comparar o custo total com o benefício medido em um processo manual equivalente. Não atribuir à IA o ganho gerado pelo motor determinístico.

As estimativas acima cobrem somente tokens do modelo, não infraestrutura, trabalho humano nem custo total da solução; são insuficientes, sozinhas, para concluir viabilidade financeira em produção.
