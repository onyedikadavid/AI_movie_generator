import logging
from typing import List, Dict, Any
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

class AgentService:
    """
    Orchestrates unscripted multi-agent character dialogue and action negotiation.
    """
    def __init__(self):
        self.ollama_url = getattr(settings, "OLLAMA_URL", "http://localhost:11434")
        self.model_name = getattr(settings, "AGENT_MODEL", "llama3")

    async def _query_agent(self, system_prompt: str, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": f"System: {system_prompt}\nUser: {prompt}",
                    "stream": False
                }
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