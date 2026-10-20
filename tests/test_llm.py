import json
import unittest
from unittest.mock import patch, MagicMock
from agent.llm import LLMReasoningEngine, DEFAULT_MODEL_FALLBACKS
from agent.state import AgentState
from agent.actions import LineupChangeAction


class TestLLMReasoningEngine(unittest.TestCase):

    def setUp(self):
        self.mock_state = AgentState()
        self.mock_state.league = {
            "name": "Persone League",
            "season": "2024",
            "scoring_type": "Half-PPR (0.5)",
        }
        self.mock_state.team = {
            "name": "Persone FC",
            "record": "3-1",
            "points_for": 450.5,
            "waiver_position": 2,
            "is_faab": False,
            "starters": [
                {"slot": "QB", "name": "Jalen Hurts", "team": "PHI", "projected": 18.0, "status": "Ativo"}
            ],
            "bench": [
                {"name": "Bench Player", "position": "RB", "projected": 6.5, "status": "Ativo"}
            ],
        }
        self.mock_state.injuries = []
        self.mock_state.pending_actions = [
            LineupChangeAction(
                bench_player_id="b1",
                start_player_id="s1",
                slot="FLEX",
                reason="Projeção superior (+3.2 pts)",
                bench_player_name="Sub In",
                start_player_name="Sub Out",
            )
        ]
        self.mock_state.free_agents = [
            {"name": "Top Waiver", "position": "WR", "team": "KC", "projected_points": 12.0}
        ]
        self.mock_state.matchup = {
            "opponent_name": "Rival FC",
            "my_projected": 108.5,
        }

    def test_model_chain_order(self):
        engine = LLMReasoningEngine()
        self.assertEqual(engine.models_chain[0], "gemini-3.5-flash-lite")
        self.assertEqual(engine.models_chain[1], "gemini-3.1-flash-lite")
        self.assertIn("gemini-3.8-flash", engine.models_chain)
        self.assertIn("gemini-3.5-flash", engine.models_chain)

    def test_custom_preferred_model(self):
        engine = LLMReasoningEngine(model="gemini-3.8-flash")
        self.assertEqual(engine.models_chain[0], "gemini-3.8-flash")
        self.assertEqual(engine.models_chain[1], "gemini-3.5-flash-lite")

    def test_model_alias_normalization(self):
        engine1 = LLMReasoningEngine(model="flash 3.5lite")
        self.assertEqual(engine1.models_chain[0], "gemini-3.5-flash-lite")
        engine2 = LLMReasoningEngine(model="flash-3.5-lite")
        self.assertEqual(engine2.models_chain[0], "gemini-3.5-flash-lite")

    @patch.object(LLMReasoningEngine, "_call_ollama")
    def test_heuristic_fallback_when_no_api_key(self, mock_ollama):
        mock_ollama.return_value = {"success": False, "source": "ollama_unavailable"}
        engine = LLMReasoningEngine(api_key="")
        result = engine.analyze("Parecer executivo", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "heuristic")
        self.assertIn("Diagnóstico Executivo", result["analysis"])
        self.assertIn("Persone FC", result["analysis"])
        self.assertIn("Rival FC", result["analysis"])
        self.assertIn("Top Waiver", result["analysis"])

    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_rest_success_primary_model(self, mock_rest):
        mock_rest.return_value = "### Parecer Gemini 3.5 Flash Lite\nExcelente escalação!"

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["model_used"], "gemini-3.5-flash-lite")
        self.assertEqual(result["analysis"], "### Parecer Gemini 3.5 Flash Lite\nExcelente escalação!")
        mock_rest.assert_called_once()

    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_rest_fallback_from_primary_to_fallback(self, mock_rest):
        # 3.5-flash-lite fails, 3.1-flash-lite succeeds
        def side_effect(model, system, user):
            if model == "gemini-3.5-flash-lite":
                raise Exception("404 Model Not Found")
            elif model == "gemini-3.1-flash-lite":
                return "### Parecer Gemini 3.1 Flash Lite Fallback\nRodada favorável."
            return ""

        mock_rest.side_effect = side_effect

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["model_used"], "gemini-3.1-flash-lite")
        self.assertIn("Gemini 3.1 Flash Lite Fallback", result["analysis"])

    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_rest_fallback_to_flagship_flash(self, mock_rest):
        def side_effect(model, system, user):
            if "lite" in model:
                raise Exception("503 High Demand")
            elif model == "gemini-3.8-flash":
                return "### Parecer Gemini 3.8 Flash\nEscalação balanceada."
            return ""

        mock_rest.side_effect = side_effect

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["model_used"], "gemini-3.8-flash")
        self.assertIn("Gemini 3.8 Flash", result["analysis"])

    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_rest_fallback_to_gemma(self, mock_rest):
        def side_effect(model, system, user):
            if "flash" in model or "lite" in model or "pro" in model:
                raise Exception("429 Quota Exceeded")
            elif model == "gemma-4-31b-it":
                return "### Parecer Gemma 4\nAnálise por modelo aberto de alta capacidade."
            return ""

        mock_rest.side_effect = side_effect

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["model_used"], "gemma-4-31b-it")
        self.assertIn("Gemma 4", result["analysis"])

    @patch.object(LLMReasoningEngine, "_discover_text_models")
    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_dynamic_discovery_fallback(self, mock_rest, mock_discover):
        mock_discover.return_value = ["gemini-future-text-preview"]
        def side_effect(model, system, user):
            if model == "gemini-future-text-preview":
                return "### Parecer Modelo Dinâmico Descoberto\nSucesso via discovery."
            raise Exception("503 Service Unavailable")

        mock_rest.side_effect = side_effect

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["model_used"], "gemini-future-text-preview")
        self.assertIn("Modelo Dinâmico Descoberto", result["analysis"])


    @patch.object(LLMReasoningEngine, "_call_ollama")
    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_gemini_rest_all_fail_triggers_heuristic(self, mock_rest, mock_ollama):
        mock_rest.side_effect = Exception("API Offline")
        mock_ollama.return_value = {"success": False, "source": "ollama_failed"}

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "heuristic")
        self.assertIn("Diagnóstico Executivo", result["analysis"])
        mock_ollama.assert_called_once()

    @patch.object(LLMReasoningEngine, "_call_ollama")
    @patch.object(LLMReasoningEngine, "_call_gemini_rest")
    def test_ollama_fallback_when_gemini_fails(self, mock_rest, mock_ollama):
        mock_rest.side_effect = Exception("Gemini Quota Exceeded")
        mock_ollama.return_value = {
            "success": True,
            "source": "ollama",
            "model_used": "qwen3.5:2b",
            "analysis": "### Parecer Ollama Local\nAnálise gerada localmente pelo modelo Qwen.",
        }

        engine = LLMReasoningEngine(api_key="test-api-key")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "ollama")
        self.assertEqual(result["model_used"], "qwen3.5:2b")
        self.assertIn("Parecer Ollama Local", result["analysis"])
        mock_ollama.assert_called_once()

    @patch.object(LLMReasoningEngine, "_call_ollama")
    def test_ollama_used_when_no_gemini_key(self, mock_ollama):
        mock_ollama.return_value = {
            "success": True,
            "source": "ollama",
            "model_used": "qwen3.5:2b",
            "analysis": "### Parecer Local Ollama Sem Chave Gemini",
        }

        engine = LLMReasoningEngine(api_key="")
        result = engine.analyze("Parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "ollama")
        self.assertEqual(result["model_used"], "qwen3.5:2b")
        self.assertIn("Parecer Local Ollama", result["analysis"])
        mock_ollama.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_call_ollama_model_selection_and_chat(self, mock_urlopen):
        tags_response = MagicMock()
        tags_response.status = 200
        tags_response.read.return_value = json.dumps({
            "models": [
                {
                    "name": "nomic-embed-text:latest",
                    "capabilities": ["embedding"],
                },
                {
                    "name": "qwen3.5:2b",
                    "capabilities": ["completion", "thinking"],
                }
            ]
        }).encode("utf-8")
        tags_response.__enter__.return_value = tags_response

        chat_response = MagicMock()
        chat_response.status = 200
        chat_response.read.return_value = json.dumps({
            "model": "qwen3.5:2b",
            "message": {
                "role": "assistant",
                "content": "### Diagnóstico Qwen\nVitória garantida com ajustes corretos.",
            }
        }).encode("utf-8")
        chat_response.__enter__.return_value = chat_response

        mock_urlopen.side_effect = [tags_response, chat_response]

        engine = LLMReasoningEngine(ollama_host="http://test-ollama:11434")
        result = engine._call_ollama("Elabore parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "ollama")
        self.assertEqual(result["model_used"], "qwen3.5:2b")
        self.assertIn("Vitória garantida", result["analysis"])

    @patch("urllib.request.urlopen")
    def test_call_ollama_fallback_to_thinking_when_content_empty(self, mock_urlopen):
        tags_response = MagicMock()
        tags_response.status = 200
        tags_response.read.return_value = json.dumps({
            "models": [{"name": "qwen3.5:2b", "capabilities": ["thinking"]}]
        }).encode("utf-8")
        tags_response.__enter__.return_value = tags_response

        chat_response = MagicMock()
        chat_response.status = 200
        chat_response.read.return_value = json.dumps({
            "model": "qwen3.5:2b",
            "message": {
                "role": "assistant",
                "content": "",
                "thinking": "### Parecer no Thinking\nTexto recuperado com sucesso.",
            }
        }).encode("utf-8")
        chat_response.__enter__.return_value = chat_response

        mock_urlopen.side_effect = [tags_response, chat_response]

        engine = LLMReasoningEngine(ollama_host="http://test-ollama:11434")
        result = engine._call_ollama("Elabore parecer", self.mock_state)

        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "ollama")
        self.assertIn("Texto recuperado com sucesso", result["analysis"])


if __name__ == "__main__":
    unittest.main()

