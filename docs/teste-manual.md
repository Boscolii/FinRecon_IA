# Teste manual com dados sintéticos

Esta amostra pequena serve para praticar o processo e explicar decisões. Ela não é representativa de produção, nem prova economia de tempo. Não consulte o gabarito até terminar sua análise.

## Preparação

Use uma pasta que não exista: o comando se recusa a substituir qualquer pasta existente.

```powershell
python -m conciliacao.demo_manual tmp\amostra-manual
```

As duas fontes podem ser examinadas em [banco.sql](../examples/amostra_manual/banco.sql) e [bandeira.csv](../examples/amostra_manual/bandeira.csv). O banco criado a partir do SQL contém 7 registros; o CSV contém 5. As datas referem-se ao mesmo dia de negócio, 19/10/2026. Uma data com `+00:00` é o mesmo instante que a data correspondente com `-03:00`.

## Procedimento

1. Tenha papel ou um arquivo para anotar os achados. Registre seu nome ou perfil de experiência, a ferramenta usada e se já conhecia a amostra.
2. Inicie um cronômetro ao começar a comparar as duas fontes e pare quando terminar o registro dos achados. Anote o tempo, inclusive eventuais interrupções.
3. Para NSUs únicos iguais, compare valor, status e horário. Se houver duplicidade, não escolha um par arbitrário.
4. Quando o CSV não tiver NSU, procure candidatos pelo estabelecimento, cartão e distância de até 24 horas; se houver mais de um candidato, marque para revisão manual. Para um candidato único, compare os valores, status e horário.
5. Anote itens encontrados somente em uma fonte. Um grupo em revisão manual não deve ser marcado também como ausente.
6. Registre para cada achado o tipo, as linhas envolvidas e, para diferença de valor, os valores dos dois lados. Depois consulte o [gabarito](gabarito-amostra-manual.md).

Para comparar com o sistema usando a mesma amostra:

```powershell
$env:CONCILIACAO_DATA_DIR = 'tmp/amostra-manual'
$env:CONCILIACAO_DB = 'tmp/amostra-manual/banco.db'
python -m conciliacao.agent "Concilie bandeira.csv no dia 2026-10-19 e apresente as divergências, inclusive os casos que exigem revisão manual."
```

Se usar esses caminhos em seu terminal, eles permanecem configurados naquela sessão até serem removidos ou o terminal ser fechado. A IA usa uma API externa e pode consumir créditos. Registre tempo e falhas observados; não atribua à IA a economia resultante do motor.

## Teste observado

Em 27/10/2026, o participante relatou 5 minutos e 30 segundos para analisar esta amostra. Ele identificou os cinco casos que exigiam atenção, mas interpretou os horários em UTC e em `-03:00` como divergentes: por exemplo, `12:00+00:00` e `09:00-03:00` são o mesmo instante. O teste manual foi relatado pelo participante, não acompanhado por um observador independente.

Uma execução real posterior do agente com Gemini, usando os mesmos arquivos e a data de negócio, apresentou os cinco achados previstos. O tempo medido externamente, incluindo iniciar e encerrar o programa, foi 9,355 segundos; o indicador interno do agente registrou 5,114 segundos, dos quais 5,084 segundos foram de modelo e 22 ms de MCP. O agente informou que as evidências retornadas estavam normalizadas em UTC, mas não explicou claramente a equivalência entre os horários do banco e do CSV solicitada no pedido.

São apenas duas observações únicas, com tarefas e condições não controladas de maneira idêntica. Elas mostram o funcionamento na amostra e uma diferença observada de tempo, mas não sustentam uma estimativa geral de produtividade nem atribuem ao modelo uma economia produzida pelo motor determinístico. Custo e tokens não foram medidos.
