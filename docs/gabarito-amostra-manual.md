# Gabarito — amostra manual

Consulte depois de terminar o teste. As linhas do CSV abaixo contam o cabeçalho como linha 1.

| Resultado esperado | Evidência |
|---|---|
| Par sem divergência | Banco ID 1 e CSV linha 2: NSU A001, R$ 10,00. |
| `value_mismatch` | Banco ID 2 e CSV linha 3 sem NSU: Padaria/cartão 2002; R$ 20,00 versus R$ 20,50. |
| `duplicate` | Banco IDs 3 e 4 e CSV linha 4: NSU D004; nenhum par deve ser selecionado. |
| `manual_review` | Banco IDs 5 e 6 e CSV linha 5 sem NSU: Café/cartão 5005; há dois candidatos próximos no tempo. |
| `only_in_bank` | Banco ID 7, NSU G007, R$ 50,00. |
| `only_in_csv` | CSV linha 6, NSU H008, R$ 60,00. |

Resumo esperado do motor: 7 registros no banco, 5 no CSV, 2 pares identificados, 1 par sem divergência e 5 achados. Os grupos de duplicidade e de revisão manual representam mais de um registro. O tempo manual deve ser anotado pelo participante; não existe tempo ou ganho presumido.
