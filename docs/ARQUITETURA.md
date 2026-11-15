# Arquitetura

## Camadas

### Interface

O Streamlit organiza o fluxo em cinco abas:

1. **Objetivo**: cadastro da prova e cálculo do ritmo-alvo.
2. **Treinos**: seleção entre Strava e demonstração.
3. **Plano**: geração, edição e aprovação humana.
4. **Analytics**: métricas e gráficos.
5. **Agente**: perguntas em linguagem natural.

### Dados

O SQLite contém:

- `athletes`;
- `goals`;
- `activities`;
- `weekly_plans`;
- `knowledge_documents`;
- índice virtual `knowledge_fts`.

No Colab, o caminho pode apontar para o Google Drive por meio de `RUNLENS_DB_PATH`. No Streamlit Cloud, o padrão é `data/runlens.db`.

### Analytics determinístico

As funções Python normalizam as atividades, calculam distância, duração, ritmo, volume semanal, cobertura cardíaca e aderência. Os gráficos são construídos em Plotly.

O LLM não é utilizado para fazer contas básicas que podem ser verificadas diretamente.

### Agentes

As ferramentas encapsulam consultas controladas ao SQLite e devolvem JSON. O `RunLens Coach` escolhe as ferramentas adequadas para responder. O `RunLens Planner` consulta o mesmo contexto antes de propor semanas de treino.

### Validação e governança

- validação de datas e número de semanas;
- limite de sessões conforme disponibilidade;
- separação entre proposta e persistência;
- revisão humana obrigatória;
- declaração de ausência e qualidade dos dados;
- avisos de caráter educacional.

## Fluxo do plano

```text
Objetivo salvo
    |
Histórico + qualidade + Knowledge Base
    |
RunLens Planner / Gemini
    |
JSON estruturado
    |
validação determinística
    |
editor Streamlit
    |
aprovação humana
    |
SQLite
```

## Fluxo de uma pergunta

```text
Pergunta
   |
RunLens Coach
   |
seleção de ferramentas
   |
objetivo / treinos / plano / KB
   |
resposta: resumo + evidências + interpretação + limitações + fontes
```
