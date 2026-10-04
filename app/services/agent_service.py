import logging
import re
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
        if not raw_url.strip():
            # Same guard as LLMService - fails clearly instead of silently
            # building a broken "https:/api/generate" URL from an empty string.
            raise RuntimeError(
                "OLLAMA_URL is not configured and the dynamic Upstash lookup "
                "for 'dynamic:ollama_url' also failed or returned nothing. "
                "Set OLLAMA_URL in .env to a real value, or make sure your "
                "Ollama notebook has published its current URL to Upstash."
            )
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
        turns: int = 4,
    ) -> List[Dict[str, str]]:
        """
        Runs a short back-and-forth between two character agents. Each turn
        returns the spoken line, a physical action AND an emotion - the emotion
        later shapes how the line is delivered (rate / pitch / volume).
        """
        history: List[Dict[str, str]] = []
        scene_context = f"Setting: {scene_goal}. Characters present: {character_a['name']}, {character_b['name']}."

        for i in range(turns):
            active = character_a if i % 2 == 0 else character_b
            other = character_b if i % 2 == 0 else character_a
            is_last = i == turns - 1

            system_prompt = (
                f"You are {active['name']}. Persona: {active.get('description', '')}. "
                f"You are in this scene: {scene_goal}. React naturally to what {other['name']} says or does. "
                "Speak the way a real person speaks out loud - short, direct, emotional, in natural "
                "conversational English. One to two sentences, under 25 words. Never describe actions inside "
                "the spoken line, never say your own name, never narrate."
            )
            recent = "\n".join(
                f"{h['speaker']} ({h['expression']}): {h['text']}" for h in history[-3:]
            ) or "(you speak first)"
            ending = " This is the last line of the exchange - bring it to a natural close." if is_last else ""
            user_prompt = (
                f"Context:\n{scene_context}\n\nConversation so far:\n{recent}\n\n"
                f"Reply with EXACTLY these three lines and nothing else:{ending}\n"
                "Emotion: <one or two words, e.g. angry, pleading, sad, joyful, stern, afraid>\n"
                "Action: <a short physical action, e.g. kneels, turns away, clenches fists>\n"
                "Text: <what you say out loud>"
            )

            raw = await self._query_agent(system_prompt, user_prompt)
            emotion, action, text = self._parse_turn(raw, active["name"])
            if not text:
                continue
            history.append({"speaker": active["name"], "text": text, "action": action, "expression": emotion})

        return history

    @staticmethod
    def _parse_turn(raw: str, speaker: str):
        """Pull Emotion / Action / Text out of the model's reply, tolerating the
        formatting slips small models make."""
        emotion, action = "neutral", "stands attentively"
        text = ""
        found_text = False
        for line in (raw or "").splitlines():
            m = re.match(r"^\s*[*_#>\-\s]*(emotion|expression|action|text|says?|dialogue|line)\s*[:\-]\s*(.*)$", line, re.IGNORECASE)
            if not m:
                if found_text and line.strip():
                    text += " " + line.strip()  # a spoken line wrapped onto a second line
                continue
            key, value = m.group(1).lower(), m.group(2).strip()
            if key in ("emotion", "expression"):
                emotion = value.strip("*_ ").lower() or emotion
            elif key == "action":
                action = value.strip("*_ ") or action
            else:
                text, found_text = value, True
        if not found_text:  # the model ignored the format: use the whole reply
            text = re.sub(r"^\s*" + re.escape(speaker) + r"\s*:\s*", "", (raw or "").strip(), flags=re.IGNORECASE)
        text = re.sub(r"^\s*" + re.escape(speaker) + r"\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\*[^*]*\*|\([^)]*\)|\[[^\]]*\]", " ", text)
        text = re.sub(r"\s+", " ", text).strip().strip('"\u201c\u201d').strip()
        return emotion[:40], action[:80], text
