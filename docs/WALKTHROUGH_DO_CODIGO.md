# Walkthrough do código

## 1. Configuração

`get_setting` procura cada configuração primeiro em variáveis de ambiente e depois em `st.secrets`. Isso permite executar o mesmo `app.py` no Colab, localmente ou no Streamlit Cloud.

## 2. Banco de dados

`initialize_database` cria automaticamente as tabelas. As funções de persistência usam queries parametrizadas e conexões curtas.

## 3. Objetivo

`save_goal` valida e persiste o objetivo. `load_athlete_goal` acrescenta informações derivadas, como ritmo-alvo e semanas até a prova.

## 4. Treinos

`load_strava_activities` consulta `/athlete/activities`, pagina resultados e mantém modalidades de corrida. `make_demo_activities` cria uma base sintética reproduzível para a apresentação.

## 5. Normalização e qualidade

As atividades são convertidas para um DataFrame analítico. Distância, duração, ritmo e validade são calculados de maneira determinística. `calculate_data_quality` evita tratar campos ausentes como zero.

## 6. Métricas semanais

`calculate_weekly_metrics` agrega o histórico por semana. `compare_plan_to_actual` cria as métricas de aderência ao plano aprovado.

## 7. Visualizações

As funções `chart_*` criam gráficos Plotly para volume, ritmo e planejado versus realizado. Mensagens vazias orientam o usuário quando ainda não há dados.

## 8. Knowledge Base

`initialize_knowledge_base` cria os documentos e o índice FTS5. `search_knowledge` transforma a pergunta em uma consulta segura e devolve documentos com fonte.

## 9. Ferramentas CrewAI

`build_runlens_tools` disponibiliza consultas específicas ao agente. Cada ferramenta limita o escopo dos dados e devolve JSON serializável.

## 10. RunLens Coach

`ask_runlens` instancia a persona especialista e exige uma resposta estruturada com resumo, evidências, interpretação, próximo passo, limitações e fontes.

## 11. RunLens Planner

`generate_training_plan` define as semanas permitidas, obriga consulta às ferramentas e pede JSON. O retorno passa por parsing, normalização e validações antes de chegar à interface.

## 12. Interface e aprovação

As cinco abas Streamlit conectam os componentes. Na aba Plano, o `st.data_editor` permite revisão. Somente o botão **Aprovar e salvar plano** chama `save_weekly_plan`.

## 13. Execução no Colab

O notebook demonstra instalação, Secrets, banco, analytics e lançamento por ngrok. Para avaliação pública, recomenda-se o deploy no Streamlit Community Cloud; ngrok permanece como alternativa de laboratório.
