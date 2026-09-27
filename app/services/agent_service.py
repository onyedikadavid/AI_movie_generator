import logging
from typing import List, Dict, Any
import httpx
from app.core.config import settings
from app.services.dynamic_config import resolve_url, KEY_OLLAMA_URL
from app.services.http_retry import post_with_retry

logger = logging.getLogger(__name__)

class AgentService:
    """
    Orchestrates unscripted multi-agent character dialogue and action negotiation.
    """
    def __init__(self):
        self.model_name = getattr(settings, "AGENT_MODEL", "llama3")

    async def _query_agent(self, system_prompt: str, prompt: str) -> str:
        # Resolved fresh on every call (not cached in __init__), same as
        # LLMService - picks up the live Colab/Kaggle URL from the Upstash
        # dynamic-config lookup instead of trusting a static, likely-stale
        # OLLAMA_URL (previously this fell straight through to the
        # http://localhost:11434 default, which is why this specific call
        # path - unlike the main script-breakdown call - kept failing with
        # "All connection attempts failed").
        raw_url = resolve_url(KEY_OLLAMA_URL, settings.OLLAMA_URL)
        if not raw_url.startswith(("http://", "https://")):
            raw_url = f"https://{raw_url}"
        ollama_url = raw_url.rstrip("/")

        # Same header strategy as LLMService, for consistency across every
        # call path that hits the tunneled Ollama endpoint.
        headers = {
            "ngrok-skip-browser-warning": "true",
            "bypass-tunnel-reminder": "true",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await post_with_retry(
                client,
                f"{ollama_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": f"System: {system_prompt}\nUser: {prompt}",
                    "stream": False
                },
                headers=headers,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama API request failed with status {response.status_code}: {response.text[:300]}"
                )
            data = response.json()
            return data.get("response", "").strip()

    async def simulate_interaction(
        self, 
        character_a: Dict[str, str], 
        character_b: Dict[str, str], 
        scene_goal: str, 
        turns: int = 4
    ) -> List[Dict[str, str]]:
        """
        Runs an autonomous dialogue exchange between two character agents.
        """
        history = []
        scene_context = f"Setting: {scene_goal}. Characters present: {character_a['name']}, {character_b['name']}."

        for i in range(turns):
            # Select active speaker
            active = character_a if i % 2 == 0 else character_b
            other = character_b if i % 2 == 0 else character_a
            
            system_prompt = (
                f"You are {active['name']}. Persona: {active.get('description', '')}. "
                f"Your goal in this scene: {scene_goal}. Adapt to what {other['name']} says or does."
            )
            
            recent_dialogue = "\n".join([f"{h['speaker']}: {h['text']} [Action: {h['action']}]" for h in history[-3:]])
            user_prompt = f"Context:\n{scene_context}\n\nRecent History:\n{recent_dialogue}\n\nRespond with your spoken line and physical action in format:\nAction: <action>\nText: <dialogue>"

            raw_response = await self._query_agent(system_prompt, user_prompt)
            
            # Basic parsing of action and text
            action = "stands attentively"
            text = raw_response
            if "Action:" in raw_response and "Text:" in raw_response:
                try:
                    parts = raw_response.split("Text:")
                    action = parts[0].replace("Action:", "").strip()
                    text = parts[1].strip()
                except Exception:
                    pass

            turn_data = {
                "speaker": active["name"],
                "text": text,
                "action": action,
                "expression": "dynamic expression"
            }
            history.append(turn_data)

        return history