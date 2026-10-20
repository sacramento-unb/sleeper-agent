"""Google Gemini Flash reasoning engine with automatic model fallback, Ollama local model fallback, and heuristic fallback."""

from __future__ import annotations
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.request
import urllib.error

from agent.actions import Action
from agent.state import AgentState

logger = logging.getLogger(__name__)


def normalize_model_name(name: str) -> str:
    """Normalize model identifiers and common human-friendly aliases."""
    if not name:
        return ""
    clean = name.strip()
    lower = clean.lower()

    # Aliases for 3.5-flash-lite (preferred primary model)
    if lower in (
        "flash 3.5lite",
        "flash 3.5 lite",
        "flash-3.5lite",
        "flash-3.5-lite",
        "flash3.5lite",
        "3.5-flash-lite",
        "3.5lite",
        "gemini-3.5lite",
        "gemini-3.5-lite",
        "gemini-flash-3.5-lite",
    ):
        return "gemini-3.5-flash-lite"

    # Other flash-lite aliases
    if lower in ("flash 3.1lite", "flash 3.1 lite", "flash-3.1lite", "flash-3.1-lite", "3.1-flash-lite"):
        return "gemini-3.1-flash-lite"
    if lower in ("flash 2.5lite", "flash 2.5 lite", "flash-2.5lite", "flash-2.5-lite", "2.5-flash-lite"):
        return "gemini-2.5-flash-lite"
    if lower in ("flash 2.0lite", "flash 2.0 lite", "flash-2.0lite", "flash-2.0-lite", "2.0-flash-lite"):
        return "gemini-2.0-flash-lite"

    # Flagship flash aliases
    if lower in ("flash 3.8", "flash-3.8", "3.8-flash", "3.8"):
        return "gemini-3.8-flash"
    if lower in ("flash 3.7", "flash-3.7", "3.7-flash", "3.7"):
        return "gemini-3.7-flash"
    if lower in ("flash 3.5", "flash-3.5", "3.5-flash", "3.5"):
        return "gemini-3.5-flash"

    # Prefix with gemini- if provided without prefix and has valid format
    if lower.startswith("3.") and ("flash" in lower or "pro" in lower) and not lower.startswith("gemini-"):
        return f"gemini-{lower}"

    return clean


# Comprehensive fallback sequence:
# 1. Primary Preference: Ultra-low latency, high-quota Flash-Lite (gemini-3.5-flash-lite)
# 2. Other Flash-Lite models (3.1, 2.5, 2.0, 1.5-8b)
# 3. Next-Gen Flagship Flash models (3.8, 3.7, 3.6, 3.5, 2.5, 2.0, 1.5)
# 4. Pro / Deep Reasoning Fallbacks (3.1-pro, 2.5-pro, 1.5-pro)
# 5. High Quota Open Models (Gemma 4, 3, 2)
DEFAULT_MODEL_FALLBACKS = [
    # Tier 1: Primary Preferred Model & High-Quota Flash-Lite Models
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-preview",
    "gemini-1.5-flash-8b",

    # Tier 2: Next-Gen Flagship Flash Models
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",

    # Tier 3: Pro / Deep Reasoning Models
    "gemini-3.1-pro-preview",
    "gemini-pro-latest",
    "gemini-2.5-pro",
    "gemini-1.5-pro",

    # Tier 4: Open Instruction Models with Massive Daily Quota (Gemma)
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "gemma-3-27b-it",
    "gemma-2-27b-it",
    "gemma-2-9b-it",
]



class LLMReasoningEngine:
    """
    Reasoning engine powered by Google Gemini Flash with model fallback:
    Preference: gemini-3.5-flash-lite -> gemini-3.1-flash-lite -> gemini-3.8-flash -> ...
    If API key is missing or all models are unavailable, attempts a local model via Ollama.
    If Ollama is also unavailable, falls back to deterministic heuristic reasoning.
    """

    _model_cooldowns: Dict[str, float] = {}

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        models_chain: Optional[List[str]] = None,
        ollama_host: Optional[str] = None,
        ollama_model: Optional[str] = None,
    ):
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip()

        # Build candidate models chain
        raw_preferred = model or os.getenv("GEMINI_MODEL", "").strip()
        preferred_model = normalize_model_name(raw_preferred)
        chain = []
        if preferred_model:
            chain.append(preferred_model)
        for m in (models_chain or DEFAULT_MODEL_FALLBACKS):
            norm_m = normalize_model_name(m)
            if norm_m not in chain:
                chain.append(norm_m)
        self.models_chain = chain
        self.tools: Dict[str, Callable[..., Any]] = {}

        self.ollama_host = (ollama_host or os.getenv("OLLAMA_HOST") or "").strip()
        self.ollama_model = (ollama_model or os.getenv("OLLAMA_MODEL") or "").strip()

    def register_tool(self, name: str, func: Callable[..., Any]) -> None:
        self.tools[name] = func

    def call_tool(self, tool_name: str, **kwargs) -> Any:
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        return self.tools[tool_name](**kwargs)

    def analyze(self, prompt: str, state: AgentState) -> Dict[str, Any]:
        """
        Processes prompt using Google Gemini Flash across the fallback chain.
        If Gemini fails or no API key is provided, attempts local model via Ollama.
        If Ollama is also unavailable or fails, executes local heuristic reasoning.
        """
        if self.api_key:
            res = self._call_gemini_chain(prompt, state)
            if res.get("success"):
                return res

        # Secondary fallback: local model via Ollama
        ollama_res = self._call_ollama(prompt, state)
        if ollama_res.get("success"):
            return ollama_res

        # Final fallback: local deterministic heuristic engine
        return self._heuristic_reasoning(prompt, state)

    def _build_context_dict(self, state: AgentState) -> Dict[str, Any]:
        """Extract structured context from AgentState."""
        return {
            "league_name": state.league.get("name"),
            "season": state.league.get("season"),
            "scoring_format": state.league.get("scoring_type", "Half-PPR (0.5 pts por recepção)"),
            "waiver_system": "Prioridade / Rolling (Sem FAAB)" if not state.team.get("is_faab") else f"FAAB (${state.team.get('faab_remaining', 0)})",
            "my_team": {
                "name": state.team.get("name"),
                "record": state.team.get("record"),
                "points_for": state.team.get("points_for"),
                "waiver_position": state.team.get("waiver_position"),
            },
            "starters": state.team.get("starters", []),
            "bench": state.team.get("bench", []),
            "injuries": state.injuries,
            "lineup_warnings": [a.reason for a in state.pending_actions if "start" in a.action_type.value or "swap" in a.action_type.value],
            "top_waiver_targets": state.free_agents[:6] if state.free_agents else [],
            "drop_candidates": state.drop_candidates[:5] if state.drop_candidates else [],
            "opponent": {
                "name": state.matchup.get("opponent_name", "Sem oponente"),
                "projected_points": state.matchup.get("opponent_projected", 0.0),
                "my_projected_points": state.matchup.get("my_projected", 0.0),
                "projected_spread": state.matchup.get("projected_diff", 0.0),
                "key_starters": state.matchup.get("opponent_key_starters", []),
            },
        }

    def _call_gemini_chain(self, prompt: str, state: AgentState) -> Dict[str, Any]:
        """Attempts Gemini generation iterating across the fallback models chain."""
        context_data = self._build_context_dict(state)
        system_instruction = (
            "Você é o Diretor Geral (General Manager) e Estrategista Sênior de Fantasy Football da Persone.\n"
            "Sua missão é fornecer um parecer executivo, direto ao ponto, assertivo e altamente estratégico.\n"
            "Analise a liga, escalação atual, banco de reservas, lesões, prioridades de waiver wire e o confronto semanal.\n"
            "Estruture sua resposta em português (Brasil) com Markdown elegante contendo:\n"
            "### 🎯 Diagnóstico Executivo da Semana\n"
            "Uma avaliação objetiva do momento do time, chances de vitória e nível de risco.\n\n"
            "### 🔄 Recomendações de Escalação (Start / Sit)\n"
            "Quais jogadores devem ser mantidos como titulares absolutos e quem corre risco ou deve ir para o banco.\n\n"
            "### ⚡ Prioridades de Waiver Wire & Gestão de Elenco\n"
            "Análise estratégica de waivers conectada às necessidades reais do elenco. Para cada alvo recomendado, destaque seu encaixe tático (ex: substituição de titular lesionado/inativo, upgrade direto ou profundidade escassa) e aponte expressamente qual jogador do elenco deve ser cortado (drop) em contrapartida, justificando a troca.\n\n"
            "### ⚔️ Estratégia do Confronto Semanal\n"
            "Chaves para superar o adversário da rodada.\n\n"
            "DIRETRIZES CRÍTICAS DE STATUS E LESÕES:\n"
            "- Jogadores com status 'NA' (Not Active / Inativo), 'OUT', 'IR', 'PUP' ou 'SUS' NÃO jogam na semana (projeção 0.0 pts).\n"
            "- NUNCA recomende colocar como titular um jogador inativo ou com status 'NA', 'OUT', 'IR', 'PUP' ou 'SUS'.\n"
            "- Se um jogador de renome (como Josh Jacobs) estiver inativo ou com status 'NA', alerte que ele NÃO VAI A CAMPO e deve permanecer no banco de reservas.\n"

        )

        user_content = (
            f"Contexto do Time e Liga:\n```json\n{json.dumps(context_data, ensure_ascii=False, indent=2)}\n```\n\n"
            f"Instrução do GM: {prompt}"
        )

        client = None
        try:
            from google import genai
            from google.genai import types
            # Minimum allowed deadline for Google GenAI SDK is 10s (10000ms).
            # Set timeout=10000 and 1 attempt (no retries) so failing/overloaded models fail immediately to next candidate
            client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=10000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        except Exception as e:
            logger.debug(f"google-genai SDK not available or client init failed: {e}; using direct REST API.")

        tried_models = set()
        now = time.time()
        start_time = now
        is_test = bool(self.api_key and self.api_key.startswith("test-"))
        gemini_budget_deadline = start_time + (9999.0 if is_test else 25.0)  # Max 25s total in production

        # 1. Iterate through configured models chain (preferred model + static fallbacks)
        for model_name in self.models_chain:
            if time.time() > gemini_budget_deadline:
                logger.warning("Gemini attempts reached 25s budget deadline. Switching immediately to local Ollama model.")
                break
            if model_name in tried_models:
                continue
            if not is_test and now < self._model_cooldowns.get(model_name, 0):
                remaining = int(self._model_cooldowns[model_name] - now)
                logger.info(f"Skipping model '{model_name}' (cooldown active for {remaining}s).")
                continue
            tried_models.add(model_name)
            text = self._try_generate_text(model_name, system_instruction, user_content, client=client, is_test=is_test)
            if text:
                return {
                    "success": True,
                    "source": "gemini",
                    "model_used": model_name,
                    "analysis": text,
                }

        # 2. Dynamic discovery: if all configured models failed, query available models for this API key
        if time.time() <= gemini_budget_deadline:
            discovered_models = self._discover_text_models()
            for model_name in discovered_models:
                if time.time() > gemini_budget_deadline:
                    logger.warning("Gemini budget deadline reached during discovery. Proceeding to Ollama.")
                    break
                if model_name in tried_models:
                    continue
                if not is_test and now < self._model_cooldowns.get(model_name, 0):
                    continue
                tried_models.add(model_name)
                logger.info(f"Attempting dynamically discovered text model: {model_name}")
                text = self._try_generate_text(model_name, system_instruction, user_content, client=client, is_test=is_test)
                if text:
                    return {
                        "success": True,
                        "source": "gemini",
                        "model_used": model_name,
                        "analysis": text,
                    }

        logger.warning("All Gemini/Gemma text models in fallback chain failed or timed out. Attempting local model via Ollama.")
        return {"success": False, "source": "gemini_failed"}

    def _try_generate_text(
        self,
        model_name: str,
        system_instruction: str,
        user_content: str,
        client: Optional[Any] = None,
        is_test: bool = False,
    ) -> Optional[str]:
        """Attempt text generation via SDK, with immediate fallback to REST API."""
        if client is not None:
            try:
                from google.genai import types
                logger.info(f"Attempting Gemini generation with SDK model: {model_name}")
                resp = client.models.generate_content(
                    model=model_name,
                    contents=user_content,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.3,
                    ),
                )
                text = resp.text
                if text and text.strip():
                    if not is_test:
                        self._model_cooldowns.pop(model_name, None)
                    return text.strip()
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"Gemini SDK failed for model '{model_name}': {e}.")
                cooldown_triggers = [
                    "503", "unavailable", "high demand", "429", "quota",
                    "resource_exhausted", "deadline_exceeded", "timed out", "timeout"
                ]
                if not is_test and any(x in err_str for x in cooldown_triggers):
                    # Mark in cooldown immediately so subsequent requests and REST don't waste time on it
                    self._model_cooldowns[model_name] = time.time() + 300
                    logger.info(f"Model '{model_name}' marked in 5-minute cooldown due to quota or service overload.")
                    return None
                logger.info(f"Trying REST fallback for model '{model_name}'...")

        try:
            logger.info(f"Attempting Gemini generation with REST API model: {model_name}")
            text = self._call_gemini_rest(model_name, system_instruction, user_content)
            if text and text.strip():
                if not is_test:
                    self._model_cooldowns.pop(model_name, None)
                return text.strip()
        except Exception as e:
            err_str = str(e).lower()
            logger.warning(f"Gemini REST call failed for model '{model_name}': {e}.")
            cooldown_triggers = [
                "503", "unavailable", "high demand", "429", "quota",
                "resource_exhausted", "deadline_exceeded", "timed out", "timeout"
            ]
            if not is_test and any(x in err_str for x in cooldown_triggers):
                self._model_cooldowns[model_name] = time.time() + 300

        return None

    def _discover_text_models(self) -> List[str]:
        """
        Dynamically query Google Gemini API to discover active text models available on the account.
        Filters out embedding, audio, image-generation, and non-conversational models.
        """
        if not self.api_key or self.api_key.startswith("test-"):
            return []
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
            req = urllib.request.Request(url, headers={"User-Agent": "SleeperAgent/1.0"}, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                discovered = []
                for m in data.get("models", []):
                    name = m.get("name", "")
                    if name.startswith("models/"):
                        name = name[len("models/"):]
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" not in methods:
                        continue
                    lower_name = name.lower()
                    skip_patterns = [
                        "tts", "image", "transcribe", "clip", "banana", "lyria",
                        "veo", "robotics", "computer-use", "embed", "dialog",
                        "deep-research", "antigravity"
                    ]
                    if any(p in lower_name for p in skip_patterns):
                        continue
                    discovered.append(name)
                return discovered
        except Exception as e:
            logger.debug(f"Dynamic Gemini model discovery failed: {e}")
            return []


    def _call_gemini_rest(self, model_name: str, system_instruction: str, user_content: str) -> str:
        """Call Gemini REST generateContent endpoint directly."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
            }
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p.get("text", "") for p in parts if "text" in p]
                return "\n".join(text_parts)
            return ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Ensure URL has scheme and no trailing slash."""
        u = url.strip().rstrip("/")
        if not u.startswith("http://") and not u.startswith("https://"):
            u = f"http://{u}"
        return u

    def _get_candidate_ollama_hosts(self) -> List[str]:
        """List candidate Ollama host URLs in priority order."""
        candidates: List[str] = []
        if self.ollama_host:
            candidates.append(self._normalize_url(self.ollama_host))

        default_hosts = [
            "http://127.0.0.1:11434",
            "http://host.docker.internal:11434",
            "http://172.17.0.1:11434",
        ]
        for host in default_hosts:
            if host not in candidates:
                candidates.append(host)
        return candidates

    def _call_ollama(self, prompt: str, state: AgentState) -> Dict[str, Any]:
        """
        Attempts reasoning using a local generative model via Ollama.
        Probes candidate hosts, selects an available generative model, and calls /api/chat.
        """
        candidate_hosts = self._get_candidate_ollama_hosts()
        working_host = None
        available_models = []

        for host in candidate_hosts:
            try:
                req = urllib.request.Request(f"{host}/api/tags", headers={"User-Agent": "SleeperAgent/1.0"}, method="GET")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        raw_models = data.get("models", [])
                        if raw_models:
                            working_host = host
                            available_models = raw_models
                            break
            except Exception:
                continue

        if not working_host or not available_models:
            logger.info("Ollama is not available on candidate hosts or has no installed models.")
            return {"success": False, "source": "ollama_unavailable"}

        # Filter out embedding-only models
        generative_models: List[str] = []
        for m in available_models:
            name = m.get("name", "")
            caps = m.get("capabilities", [])
            if caps == ["embedding"] or "embed" in name.lower() or "bert" in name.lower():
                continue
            generative_models.append(name)

        if not generative_models:
            logger.warning("Ollama is running but no generative (chat/completion) model was found.")
            return {"success": False, "source": "ollama_no_generative_models"}

        # Select model: check configured preference, priority names, or first available
        selected_model = None
        if self.ollama_model:
            for gm in generative_models:
                if gm == self.ollama_model or gm.startswith(self.ollama_model + ":") or self.ollama_model in gm:
                    selected_model = gm
                    break

        if not selected_model:
            priority_names = ["qwen", "llama", "mistral", "gemma", "phi", "deepseek"]
            for pname in priority_names:
                for gm in generative_models:
                    if pname in gm.lower():
                        selected_model = gm
                        break
                if selected_model:
                    break

        if not selected_model:
            selected_model = generative_models[0]

        logger.info(f"Invoking Ollama at {working_host} with model '{selected_model}'")

        system_instruction = (
            "Você é o Diretor Geral (General Manager) e Estrategista Chefe de Fantasy Football da Persone.\n"
            "Sua missão é fornecer um parecer executivo semanal completo, altamente analítico e aprofundado, com o mesmo nível de riqueza e detalhamento estratégico dos relatórios executivos da NFL.\n\n"
            "Estruture sua resposta estritamente em português (Brasil) com Markdown elegante, cobrindo OBRIGATORIAMENTE TODAS as 4 seções principais:\n\n"
            "### 🎯 Diagnóstico Executivo da Semana\n"
            "- Avaliação analítica detalhada do momento do time, campanha na temporada, spread (+/- pts) e nível de risco contra o oponente.\n"
            "- Análise comparativa das forças e vulnerabilidades das duas equipes.\n\n"
            "### 🔄 Recomendações de Escalação (Start / Sit)\n"
            "- **Ajustes Críticos de Escalação**: Detalhe as trocas recomendadas pelo otimizador matemático (quem entra, quem sai, projeções e ganho tático).\n"
            "- **Titulares Absolutos (Lock In)**: Destaque os pilares intocáveis do time (nomes, projeções e pontos fortes).\n"
            "- **Alerta de Inativos**: Alerte com firmeza sobre jogadores inativos (status 'NA', 'OUT' ou 'IR') que devem permanecer no banco.\n\n"
            "### ⚡ Prioridades de Waiver Wire & Gestão de Elenco\n"
            "- **Alvos Prioritários**: Análise detalhada dos melhores jogadores livres no mercado, conectando cada adição à real carência do elenco (substituição de inativos/lesionados, upgrades ou profundidade escassa).\n"
            "- **Candidatos a Corte (Drops)**: Reservas descartáveis ou excedentes para abrir espaço, pareando expressamente cada alvo de waiver ao respectivo jogador a ser cortado.\n\n"
            "### ⚔️ Estratégia do Confronto Semanal\n"
            "- **Chaves da Vitória**: Análise do matchup contra as principais ameaças do oponente, equilíbrio de piso (floor) e teto (ceiling).\n"
            "- **Orientações Finais**: Plano tático até o kickoff de domingo.\n\n"
            "DIRETRIZES CRÍTICAS:\n"
            "- Titulares com status 'Ativo' jogam normalmente.\n"
            "- Jogadores com status 'NA', 'OUT' ou 'IR' NÃO jogam (0 pts). NUNCA recomende escalar como titular um jogador inativo. Se Josh Jacobs estiver 'NA', alerte que ele NÃO JOGA e deve ficar no banco.\n"
            "- Mantenha cada seção focada, rica em dados estatísticos e garanta a conclusão completa de TODAS as 4 seções."
        )

        # Starters breakdown
        starters_lines = []
        for s in state.team.get("starters", []):
            if s.get("name") and s.get("name") != "[EMPTY]":
                f_c = f" [Piso: {s.get('floor', 0):.1f} | Teto: {s.get('ceiling', 0):.1f}]" if s.get("floor") is not None else ""
                starters_lines.append(f"- {s.get('slot')}: {s.get('name')} ({s.get('team', '')}) — Proj: {s.get('projected', 0):.1f} pts{f_c} (Status: {s.get('status', 'Ativo')})")
        starters_text = "\n".join(starters_lines) if starters_lines else "Nenhum titular cadastrado"

        # Bench breakdown
        bench_lines = []
        for b in state.team.get("bench", []):
            if b.get("name"):
                bench_lines.append(f"- {b.get('name')} ({b.get('position', '')} - {b.get('team', '')}) — Proj: {b.get('projected', 0):.1f} pts (Status: {b.get('status', 'Ativo')})")
        bench_text = "\n".join(bench_lines) if bench_lines else "Banco vazio"

        # Optimizer moves
        moves_lines = []
        for act in state.pending_actions:
            bench_p = getattr(act, "bench_player_name", "")
            start_p = getattr(act, "start_player_name", "")
            slot = getattr(act, "slot", "")
            if bench_p and start_p:
                moves_lines.append(f"- {act.reason} (Entra: {bench_p} no lugar de {start_p} no slot {slot})")
            else:
                moves_lines.append(f"- {act.reason}")
        moves_text = "\n".join(moves_lines) if moves_lines else "Nenhuma troca pendente. Escalação atual já é a ideal projetada."

        # Waivers
        waiver_lines = []
        for w in (state.free_agents or [])[:5]:
            fit = f" [Encaixe: {w.get('roster_fit')}]" if w.get('roster_fit') else ""
            drop = f" (Corte sugerido: {w.get('suggested_drop')})" if w.get('suggested_drop') else ""
            waiver_lines.append(f"- {w.get('name')} ({w.get('position', '')} - {w.get('team', '')}) — Proj: {w.get('projected_points', 0):.1f} pts{fit}{drop}")
        waiver_text = "\n".join(waiver_lines) if waiver_lines else "Nenhum jogador de destaque disponível"

        # Drop candidates
        drop_lines = []
        for d in (state.drop_candidates or [])[:5]:
            surplus = " [Excedente posicional]" if d.get("is_surplus") else ""
            drop_lines.append(f"- {d.get('name')} ({d.get('position', '')}) — Proj: {d.get('projected_points', 0):.1f} pts{surplus}: {d.get('reason', '')}")
        drop_text = "\n".join(drop_lines) if drop_lines else "Nenhum corte imediato necessário"

        # Opponent key starters
        opp_starters = state.matchup.get("opponent_key_starters", [])
        opp_starters_text = ", ".join(opp_starters) if opp_starters else "Sem dados dos titulares do oponente"

        # Injuries
        inj_lines = [f"- {i.get('name')} ({i.get('position', '')} - {i.get('team', '')}): {i.get('status')}" for i in state.injuries]
        inj_text = "\n".join(inj_lines) if inj_lines else "Nenhuma lesão impeditiva registrada"

        my_proj = state.matchup.get("my_projected", 0.0) or 0.0
        opp_proj = state.matchup.get("opponent_projected", 0.0) or 0.0
        diff = round(my_proj - opp_proj, 1) if (my_proj and opp_proj) else 0.0
        opp_name = state.matchup.get("opponent_name", "Oponente")
        waiver_pos = state.team.get("waiver_position", "N/A")
        is_faab = state.team.get("is_faab", False)
        waiver_sys = f"FAAB (${state.team.get('faab_remaining', 0)})" if is_faab else f"Prioridade #{waiver_pos} (Rolling)"

        user_content = (
            f"Equipe: {state.team.get('name')} (Campanha {state.team.get('record', '0-0')}, Projeção {my_proj:.1f} pts)\n"
            f"Adversário: {opp_name} (Projeção {opp_proj:.1f} pts, Saldo: {'+' if diff > 0 else ''}{diff:.1f} pts)\n"
            f"Sistema de Waiver: {waiver_sys}\n\n"
            f"Titulares Atuais:\n{starters_text}\n\n"
            f"Banco de Reservas:\n{bench_text}\n\n"
            f"Ajuste Recomendado pelo Otimizador Matemático:\n{moves_text}\n\n"
            f"Relatório de Lesões / Restrições:\n{inj_text}\n\n"
            f"Alvos de Destaque no Waiver Wire (com Encaixe e Corte Sugerido):\n{waiver_text}\n\n"
            f"Candidatos Avaliados para Corte (Drop Candidates):\n{drop_text}\n\n"
            f"Principais Titulares do Adversário ({opp_name}):\n{opp_starters_text}\n\n"
            f"Instrução: {prompt}"
        )

        chat_payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 450,
                "num_thread": 4,
            },
        }

        try:
            req = urllib.request.Request(
                f"{working_host}/api/chat",
                data=json.dumps(chat_payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "SleeperAgent/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                msg = data.get("message", {})
                content = (msg.get("content") or "").strip()
                if not content and msg.get("thinking"):
                    content = msg.get("thinking").strip()
                if not content and data.get("response"):
                    content = data.get("response").strip()

                if content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                    return {
                        "success": True,
                        "source": "ollama",
                        "model_used": selected_model,
                        "analysis": content,
                    }
                logger.warning(f"Ollama model '{selected_model}' returned empty text.")
                return {"success": False, "source": "ollama_empty_response"}
        except Exception as e:
            logger.warning(f"Ollama generation failed on {working_host} with model '{selected_model}': {e}")
            return {"success": False, "source": "ollama_failed"}

    def _heuristic_reasoning(self, prompt: str, state: AgentState) -> Dict[str, Any]:
        """Deterministic heuristic synthesis when Gemini API key is missing or unavailable."""
        analysis_parts = []
        team_name = state.team.get("name", "Meu Time")
        record = state.team.get("record", "0-0")
        opponent = state.matchup.get("opponent_name", "Oponente")
        opp_proj = state.matchup.get("opponent_projected")
        my_proj = state.matchup.get("my_projected")
        diff_str = ""
        if opp_proj is not None and my_proj is not None and opp_proj > 0:
            diff = round(my_proj - opp_proj, 1)
            diff_str = f" (Projeção: **{my_proj:.1f}** vs **{opp_proj:.1f}**, saldo de **{'+' if diff > 0 else ''}{diff:.1f}** pts)"

        analysis_parts.append("### 🎯 Diagnóstico Executivo da Semana")
        if opponent in ("Sem oponente (Bye / Folga)", "Bye", "Folga"):
            analysis_parts.append(f"Elenco do **{team_name}** (Campanha: **{record}**). Semana de folga (Bye) sem confronto direto agendado.")
        else:
            analysis_parts.append(f"Elenco do **{team_name}** (Campanha: **{record}**). Confronto agendado contra **{opponent}**{diff_str}.")
        
        if state.injuries:
            analysis_parts.append(f"- **Atenção Médica Urgente**: {len(state.injuries)} jogador(es) com restrições médicas no elenco.")
            for inj in state.injuries:
                analysis_parts.append(f"  * **{inj.get('name')}** ({inj.get('position')} - {inj.get('team')}): Status `{inj.get('status')}`")
        else:
            analysis_parts.append("- **Elenco Saudável**: Todos os principais titulares sem designação de lesão impeditiva.")

        analysis_parts.append("\n### 🔄 Recomendações de Escalação (Start / Sit)")
        if state.pending_actions:
            analysis_parts.append(f"Otimizador identificou **{len(state.pending_actions)} ajustes recomendados** para a rodada:")
            for act in state.pending_actions:
                analysis_parts.append(f"- **{act.action_type.value.upper()}**: {act.reason}")
        else:
            analysis_parts.append("- **Escalação Otimizada**: Sua formação titular atual é a melhor combinação estatística projetada.")

        analysis_parts.append("\n### ⚡ Prioridades de Waiver Wire & Gestão de Elenco")
        if state.free_agents:
            top_fa = state.free_agents[:3]
            analysis_parts.append("Recomendações prioritárias no mercado de agentes livres alinhadas às necessidades do elenco:")
            for i, fa in enumerate(top_fa, 1):
                fit_badge = f" — *{fa.get('roster_fit')}*" if fa.get('roster_fit') else ""
                drop_info = f" | Sugestão de corte: **{fa.get('suggested_drop')}**" if fa.get('suggested_drop') else ""
                analysis_parts.append(
                    f"- **Prioridade #{i}**: **{fa.get('name')}** ({fa.get('position')} - {fa.get('team')}) — Projeção: **{fa.get('projected_points', 0):.1f} pts**{fit_badge}{drop_info}"
                )
            if state.drop_candidates:
                prime_drop = state.drop_candidates[0]
                surplus_tag = " (excedente posicional)" if prime_drop.get("is_surplus") else ""
                analysis_parts.append(f"\n*Principal candidato a corte*: **{prime_drop.get('name')}** ({prime_drop.get('position')}){surplus_tag} — {prime_drop.get('reason')}")
        else:
            analysis_parts.append("- Nenhuma carência imediata identificada nos waivers.")

        analysis_parts.append("\n### ⚔️ Estratégia do Confronto Semanal")
        if opponent in ("Sem oponente (Bye / Folga)", "Bye", "Folga"):
            analysis_parts.append(
                "Semana de folga (Bye): Foque em blindar o banco de reservas, monitorar o relatório de lesões e antecipar adições no waiver wire para as próximas rodadas."
            )
        else:
            analysis_parts.append(
                f"Foco na consistência e piso de pontos contra **{opponent}**{diff_str}. Acompanhe os relatórios de inativos 90 minutos antes do kickoff para confirmações finais de escalação."
            )

        return {
            "success": True,
            "source": "heuristic",
            "model_used": "Motor Heurístico Persone (Local)",
            "analysis": "\n".join(analysis_parts),
        }
