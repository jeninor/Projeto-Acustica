# Resumo semanal — 28/09 a 02/10/2026

Durante a semana, as atividades foram concentradas em duas frentes principais: a **validação do parser de arquivos CF2 utilizando o CLF Viewer como referência** e a **execução do experimento de desenvolvimento de software com agentes de IA e Graphify**.

## 1. Validação de arquivos CF2 com o CLF Viewer

Na frente relacionada à pesquisa acústica, o trabalho avançou na automação da extração e validação dos dados de diretividade presentes em arquivos CF2.

Foi consolidado o processo de captura dos estados de diretividade utilizando o CLF Viewer, considerando uma malha de:

- 72 posições de rotação horizontal, de 0° a 355°, com passo de 5°;
- 37 posições verticais/arco, de 0° a 180°, também com passo de 5°;
- total de **2.664 estados de orientação por modelo**.

A versão V5 da captura do *Cabinet 3D* foi validada inicialmente com os 2.664 estados, alcançando aproximadamente **343 s de execução**, com taxa próxima de **7,76 estados/s**.

Posteriormente, foram realizadas otimizações no mecanismo de navegação do CLF Viewer. A versão V6.4 conseguiu completar novamente os 2.664 estados sem diferenças ou duplicações, utilizando a estratégia **HYBRID-ROW**. O tempo associado ao movimento dos controles caiu para aproximadamente **245 s**, enquanto cerca de **65 s** ficaram relacionados à gravação e ao processamento das imagens PNG.

Também foi observado que a gravação direta no diretório compartilhado apresentou melhor desempenho do que uma estratégia baseada em cache local seguido de sincronização.

Paralelamente, foram aperfeiçoados os scripts utilizados para captura e validação. Entre as melhorias implementadas estão:

- retomada automática de experimentos já iniciados por meio de `--resume`;
- verificação de arquivos completos antes de abrir o CLF Viewer;
- tratamento de arquivos CF2 inválidos ou inexistentes;
- leitura das frequências efetivamente apresentadas pelo CLF Viewer, evitando assumir previamente quais frequências estão disponíveis;
- proteção contra capturas incorretas quando a frequência ou o estado da interface não muda;
- melhorias na extração das curvas polares, incluindo identificação dos anéis de 0, −10, −20, −30 e −40 dB.

A etapa seguinte passou a ser a **comparação quantitativa entre os valores extraídos do CLF Viewer e os valores obtidos pelo parser CF2 v5**, utilizando métricas como RMSE, MAE, bias e erro percentil.

Essa validação é importante para confirmar se a interpretação das estruturas internas dos arquivos CF2 realizada pelo parser corresponde efetivamente ao comportamento apresentado pelo software de referência.

##

## 2. Principais avanços da semana

De forma geral, a semana permitiu consolidar duas partes importantes dos trabalhos.

Na pesquisa acústica, houve avanço significativo na **automatização e confiabilidade da captura dos dados do CLF Viewer**, deixando o processo preparado para a validação quantitativa do parser CF2 em diferentes modelos de caixas acústicas.

