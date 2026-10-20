# Sleeper Fantasy League + AI/Codex Agent

Autonomous Fantasy Football General Manager and team-management assistant for Sleeper leagues.

Built according to the core architectural design:
> **API for intelligence + LLM for reasoning + Browser automation for execution + Human approval for consequential actions.**

---

## Capabilities

- **Read-Only Sleeper Intelligence**:
  - Connects to Sleeper via official public API endpoints.
  - Inspects league settings, roster positions, PPR scoring rules, and FAAB budgets.
  - Automatically loads team rosters, starters, bench, and IR slots.
  - Identifies available unrostered free agents and waiver targets.
  - Analyzes weekly head-to-head matchups and opponent rosters.
  - Tracks league transactions and FAAB spending history.
  - Daily disk caching for the Sleeper NFL player database (~10,000+ players).

- **Fantasy Analytics Engine**:
  - **Lineup Optimization**: Solves optimal weekly starting lineups based on eligibility rules (QB, RB, WR, TE, FLEX, SUPER_FLEX, K, DEF).
  - **Filtro de Jogadores Ativos da NFL**: Validação rigorosa por vínculo com as 32 franquias ativas (`VALID_NFL_TEAMS`), expurgando lendas aposentadas (como Tom Brady, Drew Brees, etc.) de waivers e projeções.
  - **Injury & Risk Alerts**: Detects questionable, doubtful, and out players with projected scoring discounts.
  - **Waiver & FAAB Optimizer**: Prioritizes waiver claims by team positional deficits and recommends disciplined FAAB percentages (suportando ligas com ou sem FAAB / Rolling Waivers).
  - **Trade Evaluator**: Analyzes trades balancing positional scarcity, net points/week, and injury risks.

- **Parecer da IA / GM Insights (Google Gemini Flash & Fallback Ollama Local)**:
  - **Hierarquia de 3 Níveis de Raciocínio**:
    1. **Nível 1 (Nuvem / Gemini Flash-Lite & Flash)**: Prioridade para `gemini-3.5-flash-lite` (baixa latência e alta cota), com cascata automática para outros modelos Flash-Lite (`3.1-flash-lite`, `2.5-flash-lite`), modelos Flash (`3.8-flash`, `3.7-flash`, `3.6-flash`, `3.5-flash`, `2.5-flash`), modelos Pro e modelos abertos Gemma.
    2. **Nível 2 (Local / Ollama)**: Caso o Gemini falhe (indisponibilidade, erro 503, esgotamento de cota ou ausência de chave de API), o sistema conecta-se automaticamente a um modelo local via **Ollama** (ex: `qwen3.5:2b`, `llama3`, etc.), suprimindo tokens de *thinking* para latência mínima e gerando o parecer tático completo.
    3. **Nível 3 (Motor Heurístico Local)**: Caso nem a nuvem nem o Ollama estejam acessíveis, o motor determinístico embutido sintetiza as métricas e alertas de escalação.
  - **Diagnóstico Executivo da Rodada**: Relatório executivo completo em português com diagnóstico tático, titulares absolutos (*Lock & Load*), trocas críticas de titulares (*Start/Sit*), prioridades de *Waiver Wire* e chaves para a vitória contra o adversário real da semana.

- **Autenticação, RBAC e Persistência SQLite**:
  - **Acesso Restrito**: Somente emails pré-cadastrados pelo administrador (`sacramento.unb@gmail.com`) têm permissão de acesso.
  - **Primeiro Acesso**: Ativação com criação de senha segura armazenada via **PBKDF2-HMAC-SHA256 com 100.000 iterações e salt criptográfico individual de 16 bytes**.
  - **Gestão de Senha do Administrador**: Suporte a variável `ADMIN_PASSWORD` no `.env` e comandos CLI dedicados para alteração e reset de senha.
  - **Persistência Fora do Docker**: Volume SQLite bind-mounted em `./data/sleeper_agent.db` no host, preservando usuários, sessões e cache entre recriações de containers.

- **AI & Safety Layer**:
  - **Structured Action Objects**: Formats all proposed team mutations (`lineup_change`, `waiver_claim`, `trade_proposal`, `ir_move`).
  - **Human-in-the-Loop Approval Gate**: All write mutations strictly require explicit user confirmation (`requires_approval = True`).
  - **Constraint Validation**: Guarantees moves do not violate roster constraints or overspend FAAB.

- **Browser Automation Layer**:
  - Modular browser driver (Playwright integration + safe Dry-Run Simulation mode).
  - Session cookie storage without plaintext passwords.
  - Post-action state verification: re-inspects Sleeper state after mutation, verifies changes, and generates a structured diff report.

---

## Installation

```bash
git clone <repo-url>
cd sleeper_agent

# Install dependencies (or run directly with Python 3.10+)
pip install -e .
```

Copy the environment example and configure your Sleeper username:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
SLEEPER_USERNAME=sacramento.unb@gmail.com
SLEEPER_LEAGUE_ID=1389330200994533376
SLEEPER_SEASON=2026

# Google Gemini Flash-Lite & Flash (Nuvem)
GEMINI_API_KEY=seu_api_key_do_gemini
GEMINI_MODEL=gemini-3.5-flash-lite

# Modelo Local Ollama (Fallback secundário caso Gemini falhe)
OLLAMA_HOST=http://host.docker.internal:11434
OLLAMA_MODEL=

# Senha opcional para o Administrador (sacramento.unb@gmail.com)
ADMIN_PASSWORD=sua_senha_segura

# Persistência e Rede
SQLITE_DB_PATH=/app/data/sleeper_agent.db
SLEEPER_CACHE_DIR=/app/data
PORT=8088
```


---

## CLI Usage

### 1. View Team Status
Display your league record, points, starters, bench, remaining FAAB, and next opponent:
```bash
python -m sleeper_agent status --user <username>
```

### 2. Lineup Optimization & Start/Sit
Analyze current starting lineup against optimal projections and flag inactive players:
```bash
python -m sleeper_agent lineup --user <username>

# To automatically queue recommended swaps as approval-gated actions:
python -m sleeper_agent lineup --user <username> --optimize
```

### 3. Waiver Wire & FAAB Recommendations
Rank top available waiver wire targets and identify bottom-tier bench players to drop:
```bash
python -m sleeper_agent waivers --user <username> --top 5
```

### 4. Matchup Preview
Compare your starters vs. your opponent's lineup with projected point margins:
```bash
python -m sleeper_agent matchup --user <username>
```

### 5. Trade Evaluation
Evaluate the impact of proposed trades on weekly projections and depth:
```bash
python -m sleeper_agent trade --user <username> --give "Saquon Barkley" --receive "Justin Jefferson"
```

### 6. Weekly GM Routines
Run automated checklists for specific days of the week:
```bash
# Tuesday: Waiver preparation & FAAB bids
python -m sleeper_agent workflow tuesday --user <username>

# Thursday: Pre-TNF injury checks and early lineup swaps
python -m sleeper_agent workflow thursday --user <username>

# Sunday: Kickoff active/inactive announcements and lineup lock
python -m sleeper_agent workflow sunday --user <username>
```

### 7. Approving & Executing Actions
Inspect and approve pending actions:
```bash
python -m sleeper_agent exec <action_id>
```

### 8. Gestão de Senha do Administrador via CLI
Você pode alterar ou resetar a senha da conta de administrador (`sacramento.unb@gmail.com`) diretamente via terminal:
```bash
# Definir/alterar a senha do administrador diretamente:
python -m sleeper_agent admin-password <nova_senha_min_6_chars>

# Ou via Docker container:
docker exec sleeper_agent_app python -m sleeper_agent admin-password <nova_senha_min_6_chars>

# Resetar o administrador para o estado de "Primeiro Acesso" (para cadastrar nova senha na web):
python -m sleeper_agent reset-admin

# Ou via Docker container:
docker exec sleeper_agent_app python -m sleeper_agent reset-admin
```

### 9. Web Graphical Interface (Persone / Agência Jung Inspired)
Launch the interactive web dashboard:
```bash
python -m sleeper_agent web --port 8088
```
Acesse `http://localhost:8088`:
- **Navegação por Anos/Temporadas**: Alternância rápida entre temporadas arquivadas da liga (2026, 2025, 2024, etc.).
- **Times e Classificação**: Tabela completa com records, pontuações, prioridades de waiver wire e avatares.
- **Painel do Time (Dashboard)**:
  - Escalação atual vs. ótima (projeções baseadas no sistema de pontuação Half-PPR).
  - Alvos da Waiver Wire (somente jogadores ativos da NFL, com prioridade rolling ou FAAB).
  - Comparativo titular a titular contra o oponente real da semana.
  - Simulador de trocas com verificação de equilíbrio.
- **Parecer da IA / GM Insights**: Diagnóstico estratégico executivo com Google Gemini Flash.
- **Painel de Gestão de Usuários**: Exclusivo para o administrador pré-cadastrar novos emails e gerar tokens de redefinição de senha.

---

## Autenticação, Usuários e Gestão do Administrador

A aplicação implementa um modelo de **Controle de Acesso Baseado em Papéis (RBAC)** estrito e persistente via SQLite:

### 1. Política de Acesso Restrito
- **Nenhum usuário desconhecido pode se registrar livremente**: Apenas emails previamente cadastrados pelo administrador têm autorização de acesso ao sistema.
- **Administrador Master**: `sacramento.unb@gmail.com`.

### 2. Fluxo de Primeiro Acesso
- Ao ser pré-cadastrado pelo administrador, o usuário entra no status `pending_password`.
- No primeiro acesso, o usuário acessa a aba **"Primeiro Acesso"** na tela de login, informa seu email autorizado e define sua senha inicial (mínimo de 6 caracteres).
- A senha é criptografada e armazenada utilizando **PBKDF2-HMAC-SHA256 (100.000 iterações)** com **salt aleatório de 16 bytes** por usuário.
- O status da conta transita para `active` e uma sessão segura (HTTP-only cookie + Bearer token de 72 horas) é gerada automaticamente.

### 3. Redefinição de Senha
- **Usuários Comuns**: O administrador pode acessar o modal **"⚙️ Gestão de Usuários"**, selecionar qualquer usuário e clicar em **"Gerar Token de Redefinição"** (ou definir uma nova senha direta). O usuário insere o token na aba "Redefinir Senha" para cadastrar sua nova senha.
- **Administrador (`sacramento.unb@gmail.com`)**:
  - **Opção A (via .env)**: Adicione `ADMIN_PASSWORD=sua_senha` no arquivo `.env`. Na inicialização do container, a senha é automaticamente aplicada e ativada.
  - **Opção B (via Terminal Docker)**:
    ```bash
    docker exec sleeper_agent_app python -m sleeper_agent admin-password <nova_senha>
    ```
  - **Opção C (Reset para Primeiro Acesso)**:
    ```bash
    docker exec sleeper_agent_app python -m sleeper_agent reset-admin
    ```
    Em seguida, acesse a interface web, clique em **Primeiro Acesso** e cadastre sua senha.

---

## Parecer da IA / GM Insights (Google Gemini Flash & Fallbacks)

O sistema conta com um Diretor de Fantasy Football (GM) autônomo powered by **Google Gemini**:

- **Cadeia de Resiliência de Modelos**:
  1. `gemini-3.5-flash-lite` (Principal / Preferência: altíssima velocidade e cota de 500 RPD)
  2. Família **Flash-Lite**: `gemini-3.1-flash-lite`, `gemini-3.1-flash-lite-preview`, `gemini-flash-lite-latest`, `gemini-2.5-flash-lite`, `gemini-2.0-flash-lite`, `gemini-1.5-flash-8b`
  3. Família **Flash Flagship**: `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-flash-latest`, `gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`
  4. Família **Pro Reasoning**: `gemini-3.1-pro-preview`, `gemini-pro-latest`, `gemini-2.5-pro`, `gemini-1.5-pro`
  5. Modelos Abertos **Gemma (14.4K RPD)**: `gemma-4-31b-it`, `gemma-4-26b-a4b-it`, `gemma-3-27b-it`, `gemma-2-27b-it`
  6. **Descoberta Dinâmica da API**: Caso os modelos acima falhem, a engine consulta `/v1beta/models` diretamente para identificar novos modelos de texto ativos na conta.
  7. **Ollama Local**: Conexão com LLM local (ex: `qwen3.5:2b`, `llama3`) rodando em rede/Docker.
  8. **Motor Heurístico Local Offline**: Fallback determinístico instantâneo de contingência em caso de indisponibilidade geral de IA.
- **Diagnóstico Gerado**:
  - **Diagnóstico Executivo da Rodada**: Leitura da situação da equipe e diferencial projetado.
  - **Recomendações Start/Sit**: Identificação de titulares absolutos (*Lock & Load*) e trocas obrigatórias de titulares lesionados ou de baixo piso por reservas superiores.
  - **Waivers & Gestão de Elenco**: Recomendações disciplinadas conforme as regras da liga (Rolling Waivers vs FAAB).
  - **Estratégia do Confronto Semanal**: Detecção automática do oponente real da rodada (sem falsos "Bye") e chaves táticas para a vitória.

---

## Ambientes e Acesso

| Ambiente | Host / URL | Porta | Finalidade |
| :--- | :--- | :--- | :--- |
| **Local** | `http://localhost:8088` | `8088` | Desenvolvimento e testes locais |
| **Servidor Remoto** | `http://192.168.1.52:8089` | `8089` | Servidor de rede local (Home Server) |
| **Túnel Público (LocalTunnel)** | `https://fantasy-4k.loca.lt` | `8089` | Acesso público seguro via internet |

---

## Persistência de Dados e Banco SQLite

Para garantir que os usuários, permissões, sessões e o cache de consultas nunca sejam perdidos ao reiniciar ou recriar containers:
- O banco de dados SQLite fica localizado fora do container, no diretório `./data` do host:
  - `./data/sleeper_agent.db`: Banco relacional (tabelas `users`, `sessions`, `password_resets`, `query_cache`).
  - `./data/players_nfl.json`: Base de dados dos atletas da NFL da Sleeper.
  - `./data/agent_state.json`: Estado e ações estruturadas do agente.
- No `docker-compose.yml`, o volume é montado como bind mount:
  ```yaml
  volumes:
    - ./data:/app/data
    - ./data:/app/.cache/sleeper
  ```

---

## Docker Deployment

### Executar Localmente
```bash
docker compose up -d --build
```
Acesse a aplicação em `http://localhost:8088`.

### Executar no Servidor Remoto (192.168.1.52)
```bash
# Sincronizar arquivos para o servidor:
rsync -avz --exclude='.env' --exclude='data/' --exclude='.cache/' -e "ssh -i ~/.ssh/id_ed25519_manvi" ./ root@192.168.1.52:/root/sleeper_agent/

# Reconstruir e subir o container no servidor:
ssh -i ~/.ssh/id_ed25519_manvi root@192.168.1.52 "cd /root/sleeper_agent && docker compose up -d --build"
```
Acesse a aplicação em `http://192.168.1.52:8089` ou via LocalTunnel em `https://fantasy-4k.loca.lt`.

---

## Running Tests


Run the full automated test suite:
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## Architecture Diagram

```text
                  SLEEPER API (Public / Read)
                             |
                             v
                    Sleeper Data Layer
               (client, rosters, leagues, matchups)
                             |
                             v
                      Fantasy Engine
              (scoring, projections, lineup, waivers)
                             |
                             v
                 Agent & Reasoning Layer
               (policies, actions, state, planner)
                             |
                  [User Approval Gate]
                             |
                             v
                 Browser Automation Driver
             (Playwright / dry-run simulation)
                             |
                             v
                 Post-Action Verification
```
