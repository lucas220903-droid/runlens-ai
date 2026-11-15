# Critérios e bônus

## Requisito principal: criar um AI Agent

O projeto utiliza CrewAI com Gemini. O modelo não recebe apenas um texto pronto: ele pode escolher ferramentas para consultar o objetivo, a qualidade dos dados, as métricas semanais, os treinos recentes, a aderência ao plano e a Knowledge Base.

As ferramentas são construídas em `build_runlens_tools`. O agente conversacional é executado por `ask_runlens` e o agente planejador por `generate_training_plan`.

Essa separação combina:

1. cálculos determinísticos em Python;
2. seleção de ferramentas pelo agente;
3. interpretação em linguagem natural;
4. apresentação de evidências e limitações.

## Bônus 1: persona especialista do domínio

O `RunLens Coach — Especialista em Analytics de Corrida` possui:

- `role`: define a especialidade;
- `goal`: exige insights rastreáveis;
- `backstory`: define comportamento, tom e limites;
- regras: obrigam consulta aos dados e proíbem invenção de métricas;
- limites de segurança: não diagnosticar ou prescrever tratamento.

O `RunLens Planner — Especialista em Planejamento de Corrida` reutiliza esses princípios, mas tem a responsabilidade específica de propor o plano semanal.

## Bônus 2: Knowledge Base

A base é local e gratuita. A inicialização ocorre em `initialize_knowledge_base`:

- `knowledge_documents`: armazena título, conteúdo, fonte e URL;
- `knowledge_fts`: índice virtual FTS5;
- triggers: mantêm documentos e índice sincronizados;
- `search_knowledge`: recupera os documentos relevantes;
- ferramenta `pesquisar_base_conhecimento`: disponibiliza a busca ao agente.

O enunciado permite **Knowledge Base ou A2A**. Este projeto comprova o bônus pela Knowledge Base. Os dois agentes têm responsabilidades distintas, mas não são apresentados como uma implementação de protocolo A2A.

## Augmented Analytics

O sistema amplia a análise tradicional ao combinar:

- indicadores e gráficos verificáveis;
- detecção de qualidade/cobertura;
- comparação planejado versus realizado;
- interpretação automática;
- próximos passos contextualizados;
- explicitação de fontes e limitações.

## AI-driven insights

O plano é gerado depois que o agente consulta objetivo, disponibilidade, histórico, qualidade e KB. A resposta conversacional também usa ferramentas para fundamentar afirmações sobre o atleta.

## Human-in-the-loop

O agente planejador devolve apenas uma proposta. O usuário pode editar volume, sessões e observações. A gravação em `weekly_plans` só ocorre depois do botão **Aprovar e salvar plano**.

## Evidências para a apresentação

1. Mostrar a definição das personas no código.
2. Mostrar as ferramentas CrewAI.
3. Abrir a tabela/índice da Knowledge Base.
4. Gerar uma proposta de plano.
5. Editar e aprovar o plano.
6. Perguntar sobre aderência e verificar datas/métricas na resposta.
