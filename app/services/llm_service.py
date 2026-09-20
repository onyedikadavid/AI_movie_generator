import json
import httpx
from typing import Dict, Any
from app.core.config import settings
from app.schemas.script import ScriptDecompositionSchema
from app.services.agent_service import AgentService

class LLMService:
    def __init__(self):
        # Resolve Base URL dynamically and format trailing slashes.
        # (settings.OLLAMA_URL always has a real default - see app/core/config.py -
        # so this only matters if pointed at a tunnel like ngrok without a scheme.)
        raw_url = settings.OLLAMA_URL
        if not raw_url.startswith(("http://", "https://")):
            raw_url = f"https://{raw_url}"
        self.ollama_url = raw_url.rstrip("/")
        
        # Pull model name or default to available Llama3 build
        self.model_name = getattr(settings, "LLM_MODEL", "llama3:latest")
        self.agent_service = AgentService()

    async def generate_script_structure(
        self,
        prompt: str,
        genre_hint: str = None,
        tone_hint: str = None,
        visual_style_hint: str = None,
    ) -> ScriptDecompositionSchema:
        """
        Generates full script breakdown utilizing AgentService for character interactions.
        Optional hints come from the "New Project" form's stylistic toggles (PRD 4.2) and,
        when provided, are treated as user requirements rather than suggestions.
        """
        style_constraints = ""
        if genre_hint or tone_hint or visual_style_hint:
            style_constraints = "\n        The user has requested the following, honor them exactly:\n"
            if genre_hint:
                style_constraints += f"        - Genre: {genre_hint}\n"
            if tone_hint:
                style_constraints += f"        - Tone: {tone_hint}\n"
            if visual_style_hint:
                style_constraints += f"        - Visual style: {visual_style_hint}\n"

        # Step A: Generate Base Characters & High-Level Scene Outlines
        base_prompt = f"""
        Analyze this raw story prompt and output a valid JSON matching this structure:
        {{
            "title": "...",
            "genre": "...",
            "tone": "...",
            "visual_style": "...",
            "characters": [
                {{
                    "name": "...",
                    "description": "...",
                    "appearance_prompt": "..."
                }}
            ],
            "scenes": [
                {{
                    "scene_number": 1,
                    "location": "...",
                    "visual_description": "...",
                    "motion_prompt": "...",
                    "duration_seconds": 10.0
                }}
            ]
        }}
        {style_constraints}
        Prompt: {prompt}
        Respond ONLY with raw JSON.
        """
        
        # Combined header strategy for ngrok, localtunnel, and standard browser emulation
        headers = {
            "ngrok-skip-browser-warning": "true",
            "bypass-tunnel-reminder": "true",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/generate",
                json={"model": self.model_name, "prompt": base_prompt, "stream": False, "format": "json"},
                headers=headers
            )
            
            # Catch bad gateways (502) or forbidden edge blocks (403) before parsing
            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama API request failed with status {response.status_code}: {response.text[:300]}"
                )

            payload = response.json()
            raw_json = payload.get("response", "{}")

        parsed_data = json.loads(raw_json)

        # Step B: Run Autonomous Multi-Agent Loop for Scenes with Multiple Characters
        characters = parsed_data.get("characters", [])
        if len(characters) >= 2:
            char_a, char_b = characters[0], characters[1]
            for scene in parsed_data.get("scenes", []):
                goal = f"{scene['location']} - {scene['visual_description']}"
                # Generate dynamic turns using AgentService
                turns = await self.agent_service.simulate_interaction(char_a, char_b, goal, turns=4)
                scene["dialogue_turns"] = turns

        return ScriptDecompositionSchema(**parsed_data)