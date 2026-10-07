import json
import logging
import re
import httpx
from typing import Dict, Any
from app.core.config import settings
from app.schemas.script import ScriptDecompositionSchema
from app.services.agent_service import AgentService
from app.services.dynamic_config import resolve_url, KEY_OLLAMA_URL
from app.services.http_retry import post_with_retry

logger = logging.getLogger(__name__)


class LLMService:
    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Parse the model's JSON, tolerating code fences / stray text around it."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        raise RuntimeError(
            "The language model didn't return valid JSON for the script. This usually fixes itself "
            "if you press Retry/Resume; if it keeps happening, try a larger model in LLM_MODEL."
        )

    def __init__(self):
        # Prefer the live URL published by the Ollama Colab/Kaggle notebook
        # (see app/services/dynamic_config.py); falls back to the static
        # OLLAMA_URL from .env if dynamic config isn't set up or unreachable.
        raw_url = resolve_url(KEY_OLLAMA_URL, settings.OLLAMA_URL)
        if not raw_url.strip():
            # Both the dynamic Upstash lookup AND the static OLLAMA_URL
            # fallback came up empty (e.g. Upstash unreachable and .env has
            # OLLAMA_URL= with nothing after it). Without this check, the
            # code below would silently build "https:/api/generate" (missing
            # the "//") from an empty string, which fails deep inside httpx
            # with the confusing "Request URL is missing an 'http://' or
            # 'https://' protocol" - this fails clearly instead, right here.
            raise RuntimeError(
                "OLLAMA_URL is not configured and the dynamic Upstash lookup "
                "for 'dynamic:ollama_url' also failed or returned nothing. "
                "Set OLLAMA_URL in .env to a real value, or make sure your "
                "Ollama notebook has published its current URL to Upstash."
            )
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
        #
        # IMPORTANT: every field below is actually used downstream - leaving
        # one out of this prompt means the LLM never generates it, and it
        # silently defaults to empty/null:
        #   - image_prompt: this is EXACTLY what gets sent to the image
        #     generator (see pipeline_tasks.py). An empty image_prompt means
        #     SDXL generates from an essentially blank prompt (just the
        #     visual style tacked on), producing generic, unrelated images.
        #   - narration_text: if this is null, no speech gets generated for
        #     that scene at all (a silent placeholder clip is used instead)
        #     - it's the difference between a scene having audio or not.
        # And the scene-count instruction below matters because without it,
        # the model can (and did, in practice) compress an entire short
        # story into a single scene instead of breaking it into the several
        # scenes/shots a real short film would actually have.
        base_prompt = f"""
        Analyze this raw story prompt and break it into a short film's worth
        of scenes - typically 4 to 8 scenes for a short story, one scene per
        distinct narrative beat (e.g. arrival, confrontation, turning point,
        resolution). Do NOT compress the entire story into a single scene
        unless the prompt is genuinely a single-moment vignette.

        Output valid JSON matching this structure exactly:
        {{
            "title": "...",
            "genre": "...",
            "tone": "...",
            "visual_style": "...",
            "characters": [
                {{
                    "name": "...",
                    "description": "Role and personality in one or two sentences.",
                    "appearance_prompt": "Concrete, visual and reusable in every scene: age, build, skin tone, hair, face, clothing. Never mention the story here.",
                    "gender": "male or female (always provide - it chooses the character's voice)",
                    "age_group": "child, teen, adult or elder"
                }}
            ],
            "scenes": [
                {{
                    "scene_number": 1,
                    "location": "...",
                    "visual_description": "...",
                    "image_prompt": "A detailed, standalone text-to-image prompt for this scene's keyframe - subject(s), action, setting, time of day, lighting and camera framing (e.g. wide shot / medium shot). Sent directly to the image generator, so it must make sense on its own.",
                    "narration_text": "One or two SHORT sentences (under 25 words total) of narrator voiceover that set up this scene, written to be spoken aloud. Describe the situation - never put a character's own words here.",
                    "characters_present": ["Name1", "Name2"],
                    "motion_prompt": "Gentle, concrete camera and subject movement for a few seconds, e.g. 'slow push-in while the boy lowers his head'. Avoid fast or dramatic motion.",
                    "sound_design": "The background sounds and effects you would HEAR here - 6 to 14 words, no speech, no music. e.g. 'distant gunfire, helicopter overhead, running footsteps' or 'light wind, birds, far-off market chatter'.",
                    "duration_seconds": 5
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
            response = await post_with_retry(
                client,
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

        parsed_data = self._parse_json(raw_json)

        # Step B: character-to-character dialogue for scenes with 2+ people.
        # Speakers are the characters actually present in each scene (not
        # always the first two). One failed call never loses the whole script.
        characters = parsed_data.get("characters", []) or []
        by_name = {str(c.get("name", "")).strip().lower(): c for c in characters}
        for scene in parsed_data.get("scenes", []) or []:
            present = [by_name[n.strip().lower()] for n in (scene.get("characters_present") or [])
                       if isinstance(n, str) and n.strip().lower() in by_name]
            if len(present) >= 2:
                char_a, char_b = present[0], present[1]
            elif not present and len(characters) >= 2:
                char_a, char_b = characters[0], characters[1]
            else:
                continue  # a single person on screen: the narrator carries the scene
            goal = f"{scene.get('location', '')} - {scene.get('visual_description', '')}"
            try:
                scene["dialogue_turns"] = await self.agent_service.simulate_interaction(char_a, char_b, goal, turns=4)
            except Exception as e:  # noqa: BLE001
                logger.warning("Dialogue for scene %s failed (%s) - continuing with narration only.", scene.get("scene_number"), e)
                scene["dialogue_turns"] = []

        # Small models sometimes emit null / wrong types; normalise before validating.
        for sc in parsed_data.get("scenes", []) or []:
            for key in ("location", "motion_prompt", "image_prompt", "narration_text", "sound_design"):
                if sc.get(key) is None:
                    sc[key] = "" if key not in ("narration_text", "sound_design") else None
            if not sc.get("visual_description"):
                sc["visual_description"] = sc.get("image_prompt") or sc.get("location") or "A scene from the story."
            try:
                sc["duration_seconds"] = max(2, min(int(float(sc.get("duration_seconds") or 5)), 20))
            except (TypeError, ValueError):
                sc["duration_seconds"] = 5
            if not isinstance(sc.get("characters_present"), list):
                sc["characters_present"] = []
        for ch in parsed_data.get("characters", []) or []:
            ch["appearance_prompt"] = ch.get("appearance_prompt") or ch.get("description") or ""
            ch["description"] = ch.get("description") or ch["appearance_prompt"]

        return ScriptDecompositionSchema(**parsed_data)