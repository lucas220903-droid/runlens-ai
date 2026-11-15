# RunLens AI

Plataforma acadêmica de **Augmented Analytics e AI-Driven Insights** para corrida. O atleta informa uma prova-alvo e um tempo desejado; a aplicação analisa treinos do Strava ou dados fictícios, apresenta indicadores e utiliza agentes CrewAI com Gemini para gerar um plano e produzir feedback fundamentado.

## Demonstração

- Aplicação Streamlit: adicione aqui a URL `https://SEU-APP.streamlit.app` após o deploy.
- Notebook Colab: abra `notebooks/RunLens_Gemini_Strava_Colab.ipynb`.
- O modo **Demonstração** não exige uma conta Strava.

## Principais funcionalidades

- Cadastro do objetivo da prova e cálculo do ritmo-alvo.
- Integração opcional com a API do Strava.
- Geração reproduzível de treinos fictícios para apresentação offline.
- Persistência em SQLite.
- Verificação de qualidade e cobertura dos dados.
- Dashboard Plotly com volume, ritmo e planejado versus realizado.
- Agente `RunLens Planner` para propor o plano semanal.
- Revisão humana antes de gravar o plano.
- Agente `RunLens Coach` para responder perguntas com evidências.
- Knowledge Base local pesquisável com SQLite FTS5.

## Critérios acadêmicos

| Critério | Implementação |
|---|---|
| AI Agent | CrewAI + Gemini + ferramentas de consulta aos dados |
| Persona especialista | `RunLens Coach` e `RunLens Planner`, com `role`, `goal` e `backstory` |
| Knowledge Base | Documentos locais indexados por SQLite FTS5, com fonte e URL |
| Augmented Analytics | Métricas determinísticas enriquecidas por interpretação do agente |
| AI-driven insights | Plano e feedback baseados no objetivo e no histórico consultado |
| Human-in-the-loop | Nenhum plano gerado pela IA é salvo sem revisão e aprovação |

Detalhes em [Critérios e bônus](docs/CRITERIOS_E_BONUS.md).

## Arquitetura resumida

```text
Objetivo + Strava/dados demo
             |
             v
      SQLite + validações
             |
       +-----+----------------+
       |                      |
       v                      v
Dashboard Plotly      Ferramentas CrewAI
                              |
                +-------------+-------------+
                |                           |
                v                           v
        RunLens Planner              RunLens Coach
                |                           |
        revisão humana         resposta com evidências + KB
```

Veja [Arquitetura](docs/ARQUITETURA.md) e [Walkthrough do código](docs/WALKTHROUGH_DO_CODIGO.md).

## Executar localmente

Recomendado: Python 3.11.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
streamlit run app.py
```

Preencha `GEMINI_API_KEY` no arquivo local `secrets.toml`. Esse arquivo está ignorado pelo Git e nunca deve ser publicado.

## Publicar no Streamlit Community Cloud

1. Acesse `https://share.streamlit.io` e conecte sua conta GitHub.
2. Escolha este repositório, a branch `main` e o arquivo `app.py`.
3. Em **Advanced settings**, escolha Python 3.11.
4. Em **Secrets**, cadastre:

```toml
GEMINI_API_KEY = "sua_chave"
GEMINI_MODEL_ID = "gemini-3.1-flash-lite"
STRAVA_ACCESS_TOKEN = ""
```

O `NGROK_TOKEN` não é necessário no Streamlit Community Cloud.

## Roteiro de demonstração

1. Cadastre atleta, prova, distância, data e tempo-alvo.
2. Na aba **Treinos**, selecione **Demonstração** e gere os dados fictícios.
3. Abra **Analytics** e apresente os indicadores.
4. Em **Plano**, gere uma proposta com IA, revise e aprove.
5. Volte ao Analytics para comparar planejado e realizado.
6. Pergunte ao agente: `Estou aderindo ao plano? Mostre as evidências e as fontes.`

## Segurança e limitações

- O projeto não contém chaves, tokens nem dados pessoais.
- O token Strava digitado pela interface não é salvo no banco.
- O SQLite local do Streamlit Cloud é suficiente para demonstração, mas não deve ser considerado armazenamento permanente.
- O protótipo é educacional e não substitui treinador ou profissional de saúde.
- A integração atual usa um token Strava; OAuth completo é uma evolução futura.

## Estrutura

```text
app.py                             aplicação Streamlit
requirements.txt                  dependências fixadas
notebooks/                        versão didática em Colab
docs/ARQUITETURA.md               componentes e fluxo de dados
docs/WALKTHROUGH_DO_CODIGO.md     explicação das etapas
docs/CRITERIOS_E_BONUS.md         mapeamento do enunciado
.streamlit/                       tema e exemplo de Secrets
data/                             diretório do SQLite local
```
