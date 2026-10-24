import json
import logging
import re
import httpx
from typing import Dict, Any
from app.core.config import settings
from app.schemas.script import ScriptDecompositionSchema
from app.services.agent_service import AgentService
from app.services.style_presets import is_kids, script_constraints
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

    _HEADERS = {
        # Combined header strategy for ngrok, localtunnel, and standard browser emulation
        "ngrok-skip-browser-warning": "true",
        "bypass-tunnel-reminder": "true",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
    }

    async def _generate(self, prompt: str) -> str:
        """One JSON-mode call to the Ollama server; returns the raw text of the answer."""
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await post_with_retry(
                client,
                f"{self.ollama_url}/api/generate",
                json={"model": self.model_name, "prompt": prompt, "stream": False, "format": "json"},
                headers=self._HEADERS,
            )
            # Catch bad gateways (502) or forbidden edge blocks (403) before parsing
            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama API request failed with status {response.status_code}: {response.text[:300]}"
                )
            return response.json().get("response", "{}")

    async def _story_bible(self, prompt: str, genre_hint=None, tone_hint=None) -> dict:
        """Step 1 of the script: the model reads the idea, states what the story IS and WRITES THE FULL STORY (hook
        first, beginning-middle-end) before any scene exists. Best effort - on failure the scenes are written from the
        prompt alone. Returns the analysis keys plus "story"."""
        hints = ""
        if genre_hint or tone_hint:
            hints = f"\nRequested genre: {genre_hint or 'any'}. Requested tone: {tone_hint or 'any'}."
        ask = f"""
        You are a story editor and screenwriter. Read this story idea, understand it, and turn it into a COMPLETE short
        story that anyone can follow. Be faithful to the idea - keep its characters, names, places and events, only
        reorder and fill small gaps so it makes sense. Do not invent a different story.{hints}
        The story must: open with a HOOK (the most intriguing, surprising or emotional moment, in the first sentence or
        two, so viewers keep watching within the first 3 seconds); then set up who and where; let the problem grow, each
        event causing the next; reach a turning point; and END clearly, answering the hook. Simple, concrete words.
        Return JSON with exactly these keys:
        {{"logline": "what the story is about in one sentence",
          "main_character": "who we follow and what they want",
          "problem": "what stands in their way",
          "turning_point": "the moment everything changes",
          "ending": "how it ends",
          "message": "the meaning / feeling the viewer is left with",
          "hook": "the opening hook, one or two sentences",
          "story": "the complete story, 150 to 350 words, in short paragraphs, written in present tense, hook first, ending last"}}
        Story idea: {prompt}
        Respond ONLY with raw JSON.
        """
        try:
            data = self._parse_json(await self._generate(ask))
        except Exception as e:  # noqa: BLE001
            logger.warning("Story analysis failed (%s) - writing the scenes straight from the prompt.", e)
            return {}
        keys = ("logline", "main_character", "problem", "turning_point", "ending", "message", "hook", "story")
        return {k: str(data.get(k)).strip() for k in keys if isinstance(data, dict) and data.get(k)}

    @staticmethod
    def _story_problems(data: dict, scene_count: int = None) -> list:
        """Cheap structural checks on the LLM's script; anything found is sent back to the model once."""
        problems = []
        scenes = (data or {}).get("scenes") or []
        if scene_count and len(scenes) != int(scene_count):
            problems.append(f"the script must have exactly {int(scene_count)} scenes (it has {len(scenes)})")
        elif len(scenes) < 3 and not scene_count:
            problems.append("fewer than 3 scenes - split the story into its beginning, middle and end")
        elif not all(isinstance(sc, dict) for sc in scenes):
            return ["scenes must be JSON objects"]
        else:
            if not (scenes[0].get("narration_text") or scenes[0].get("story_beat")):
                problems.append("scene 1 has no hook")
            if len(scenes) >= 3 and not (scenes[-1].get("story_beat") or scenes[-1].get("visual_description")):
                problems.append("the last scene does not show the ending")
        if not (data or {}).get("characters"):
            problems.append("no characters were listed")
        return problems

    async def _complete_scenes(self, parsed_data: dict, genre_hint, tone_hint, visual_style_hint, secs: int) -> dict:
        """Writes each scene's dialogue and cleans up the model's output (shared by the first script and by
        story continuations). `parsed_data` needs "characters" (everyone who may appear) and "scenes"."""
        # Step B: character-to-character dialogue for scenes with 2+ people.
        # Speakers are the characters actually present in each scene (not
        # always the first two). One failed call never loses the whole script.
        characters = parsed_data.get("characters", []) or []
        by_name = {str(c.get("name", "")).strip().lower(): c for c in characters}
        for scene in parsed_data.get("scenes", []) or []:
            present = [by_name[n.strip().lower()] for n in (scene.get("characters_present") or [])
                       if isinstance(n, str) and n.strip().lower() in by_name]
            beat = scene.get("story_beat") or ""
            goal = f"{scene.get('location', '')} - {scene.get('visual_description', '')}" + (
                f". What this moment must achieve in the story: {beat}" if beat else "")
            kids = is_kids(genre_hint, tone_hint, visual_style_hint)
            if len(present) >= 2:
                char_a, char_b = present[0], present[1]
            elif not present and len(characters) >= 2:
                char_a, char_b = characters[0], characters[1]
            else:
                # A single person on screen. With a narrator, the narrator carries the scene; without one,
                # that character speaks out loud so the scene isn't silent.
                solo = present[0] if len(present) == 1 else (characters[0] if len(characters) == 1 else None)
                # Written whether or not a narrator is on, so switching the narrator off later never leaves a silent scene.
                if solo is not None:
                    try:
                        scene["dialogue_turns"] = await self.agent_service.simulate_monologue(solo, goal, lines=2, kids=kids)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Spoken lines for scene %s failed (%s).", scene.get("scene_number"), e)
                        scene["dialogue_turns"] = []
                continue
            try:
                scene["dialogue_turns"] = await self.agent_service.simulate_interaction(
                    char_a, char_b, goal, turns=(2 if secs <= 5 else 3 if secs <= 9 else 4) if (settings.SCENE_MODE or '').lower() == 'whole' else 4, kids=kids
                )
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
                sc["duration_seconds"] = max(2, min(int(float(sc.get("duration_seconds") or 5)), 120))
            except (TypeError, ValueError):
                sc["duration_seconds"] = 5
            if not isinstance(sc.get("characters_present"), list):
                sc["characters_present"] = []
        for ch in parsed_data.get("characters", []) or []:
            ch["appearance_prompt"] = ch.get("appearance_prompt") or ch.get("description") or ""
            ch["description"] = ch.get("description") or ch["appearance_prompt"]
        return parsed_data

    async def continue_story(
        self,
        story_so_far: str,
        characters: list,
        scenes_so_far: list,
        direction: str = "",
        scene_count: int = None,
        scene_seconds: int = None,
        genre_hint: str = None,
        tone_hint: str = None,
        visual_style_hint: str = None,
    ) -> dict:
        """Writes what happens NEXT in an existing film: more of the story and new scenes (numbered after the existing
        ones) with the SAME characters, looks and voices. Returns {"story_addition", "new_characters", "scenes"}."""
        n = max(1, int(scene_count or 1))
        secs = int(scene_seconds or settings.WHOLE_SCENE_MAX_SECONDS)
        start = len(scenes_so_far) + 1
        cast = "\n".join(
            f"        - {c['name']} ({c.get('gender') or '?'}, {c.get('age_group') or '?'}): {c.get('description') or ''} | LOOK: {c.get('appearance_prompt') or ''}"
            for c in characters
        )
        recap = "\n".join(
            f"        Scene {sc['scene_number']} [{sc.get('location') or ''}]: {sc.get('visual_description') or ''}"
            + (f" Said: {sc['lines']}" if sc.get("lines") else "")
            for sc in scenes_so_far[-6:]
        )
        last = scenes_so_far[-1] if scenes_so_far else {}
        want = (f"The user wants the story to continue like this: {direction.strip()}" if (direction or "").strip()
                else "The user gave no direction - continue the story in the most natural, interesting way.")
        prompt = f"""
        You are a screenwriter continuing a short film. Keep the story consistent and keep going from where it stopped.

        STORY SO FAR:
        {story_so_far or '(not written down - use the scenes below)'}

        CHARACTERS (their names, looks and personalities are FIXED - reuse them exactly, never change how they look):
{cast}

        LAST SCENES:
{recap}
        The film stopped at: scene {last.get('scene_number', 0)} in "{last.get('location') or ''}".

        {want}

        RULES
        1. Write EXACTLY {n} new scene{'s' if n != 1 else ''}, numbered from {start}. They follow directly after the last scene
           and move the story forward - never repeat what already happened.
        2. Same characters, same names, same world and tone. Only add a NEW character if the story truly needs one
           (list them in "new_characters" with a full, concrete "appearance_prompt").
        3. Each new scene is filmed as ONE continuous clip of about {secs} seconds: give it only what fits.
        4. Cause and effect: each scene follows from the one before. Keep it clear for someone who has not read anything.
        5. "continues_previous": true ONLY if the scene continues the same moment in the same place with the same people
           (the camera simply carries on); false if it is a new place or time (a normal cut).
        6. If the story already ended, continue with what happens next (a new chapter), do not restart it.

        Return JSON exactly like this:
        {{
            "story_addition": "The new part of the story in 80 to 250 words, short paragraphs, present tense.",
            "new_characters": [{{"name": "...", "description": "...", "appearance_prompt": "...", "gender": "male or female", "age_group": "child, teen, adult or elder"}}],
            "scenes": [
                {{
                    "scene_number": {start},
                    "story_beat": "One sentence: what happens and why it matters.",
                    "continues_previous": false,
                    "location": "...",
                    "visual_description": "...",
                    "image_prompt": "Detailed standalone text-to-image prompt for the keyframe.",
                    "narration_text": "One or two short narrator sentences (under 25 words), never a character's own words.",
                    "characters_present": ["Name1"],
                    "motion_prompt": "Cinematic camera direction plus what the people DO.",
                    "sound_design": "Background sounds, 6 to 14 words, no speech, no music.",
                    "duration_seconds": {secs}
                }}
            ]
        }}
        Respond ONLY with raw JSON.
        """
        data = self._parse_json(await self._generate(prompt))
        scenes = [sc for sc in (data.get("scenes") or []) if isinstance(sc, dict)]
        if not scenes:
            raise RuntimeError("The story writer didn't return any new scenes. Press Continue again.")
        scenes = scenes[:n]
        known = {c["name"].strip().lower() for c in characters}
        new_chars = [c for c in (data.get("new_characters") or [])
                     if isinstance(c, dict) and str(c.get("name", "")).strip() and str(c["name"]).strip().lower() not in known]
        for sc in scenes:
            sc["story_beat"] = sc.get("story_beat") or ""
        for i, sc in enumerate(scenes):
            sc["scene_number"] = start + i
            sc["continues_previous"] = bool(sc.get("continues_previous")) and bool(scenes_so_far or i)
        everyone = [dict(c) for c in characters] + new_chars
        completed = await self._complete_scenes(
            {"characters": everyone, "scenes": scenes}, genre_hint, tone_hint, visual_style_hint, secs
        )
        for sc, orig in zip(completed["scenes"], scenes):
            sc["scene_number"], sc["continues_previous"] = orig["scene_number"], orig["continues_previous"]
        return {
            "story_addition": str(data.get("story_addition") or "").strip(),
            "new_characters": new_chars,
            "scenes": completed["scenes"],
        }

    async def generate_script_structure(
        self,
        prompt: str,
        genre_hint: str = None,
        tone_hint: str = None,
        visual_style_hint: str = None,
        narrator: bool = None,
        scene_count: int = None,
        scene_seconds: int = None,
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
            style_constraints += script_constraints(genre_hint, tone_hint, visual_style_hint) + "\n"

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
        # Stage 1: UNDERSTAND the story. The model first reads the prompt and writes a short "story bible" (what it is
        # about, who wants what, what stands in the way, how it ends, and a hook). Stage 2 then builds the scenes
        # from that bible, so the film has one clear line from beginning to end instead of loosely related pictures.
        n_words = len((prompt or "").split())
        if scene_count:
            count_rule = (f"Write EXACTLY {int(scene_count)} scenes - no more, no fewer. Spread the whole story "
                          f"(beginning, middle and end) across them.")
        else:
            lo, hi = (3, 4) if n_words < 40 else ((4, 6) if n_words < 120 else (6, 8))
            count_rule = (f"Choose the number of scenes the story needs - for this prompt usually {lo} to {hi}. "
                          f"Every scene must be a distinct story beat.")
        secs = int(scene_seconds or settings.WHOLE_SCENE_MAX_SECONDS)
        bible = await self._story_bible(prompt, genre_hint, tone_hint)
        story_text = bible.pop("story", "") if bible else ""
        bible_text = ""
        if bible:
            bible_text = "\n        STORY BIBLE (your own analysis of the prompt - the film MUST follow it):\n" + "".join(
                f"        - {k}: {v}\n" for k, v in bible.items() if v
            )
        if story_text:
            bible_text += ("\n        THE FULL STORY (already written from the prompt - the scenes must tell EXACTLY this story, "
                           "in this order, hook first, ending last):\n        " + story_text.replace("\n", "\n        ") + "\n")

        base_prompt = f"""
        You are a screenwriter turning a raw story idea into a short film that ANYONE can follow without explanation.
        Read the prompt carefully, understand it, then restructure it into scenes with a clear beginning, middle and end.
        {bible_text}
        RULES FOR THE STORY
        1. HOOK: scene 1 must grab the viewer within the first 3 seconds - open on the most intriguing, surprising or
           emotional moment (a striking image, a problem already happening, a question the viewer wants answered).
           Do NOT open with slow scenery or a boring introduction. Its narration_text or first spoken line is the hook line.
        2. STRUCTURE: scene 1 = hook + who/where, the middle scenes = the problem grows (each scene is caused by the one
           before it, with rising stakes), the last scene = a clear, satisfying ending that answers the hook.
        3. FAITHFUL: keep the prompt's characters, names, places, events and meaning. You may reorder and fill small gaps
           so the story makes sense, but never change what the story is about and never add major characters or events
           that are not implied by the prompt.
        4. CLEAR: a viewer who has not read the prompt must understand who the people are, what they want, what goes wrong
           and how it ends. Use simple, concrete wording. One idea per scene.
        5. {count_rule} Do NOT compress the story into one scene unless the prompt is genuinely a single moment.
           Each scene is filmed as ONE continuous clip of about {secs} seconds, so give it only what fits in that time.
        6. Every scene's visuals, narration and mood must match the genre and tone of the story.

        Output valid JSON matching this structure exactly:
        {{
            "title": "A short, catchy title",
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
                    "story_beat": "One sentence: what happens in this scene and why it matters to the story (hook / setup / complication / turning point / climax / ending).",
                    "location": "...",
                    "visual_description": "...",
                    "image_prompt": "A detailed, standalone text-to-image prompt for this scene's keyframe - subject(s), action, setting, time of day, lighting and camera framing (e.g. wide shot / medium shot). Sent directly to the image generator, so it must make sense on its own.",
                    "narration_text": "One or two SHORT sentences (under 25 words total) of narrator voiceover that set up this scene, written to be spoken aloud. Describe the situation - never put a character's own words here. In scene 1 this is the HOOK line.",
                    "characters_present": ["Name1", "Name2"],
                    "motion_prompt": "Cinematic camera direction plus what the people DO, for a few seconds, like a real film: e.g. 'slow dolly-in on the boy as he lowers his head, then a handheld pan to the door' or 'tracking shot following her down the corridor'. Vary the camera from scene to scene (push-in, tracking, pan, handheld, low angle).",
                    "sound_design": "The background sounds and effects you would HEAR here - 6 to 14 words, no speech, no music. e.g. 'distant gunfire, helicopter overhead, running footsteps' or 'light wind, birds, far-off market chatter'.",
                    "duration_seconds": {secs}
                }}
            ]
        }}
        {style_constraints}
        Prompt: {prompt}
        Respond ONLY with raw JSON.
        """

        raw_json = await self._generate(base_prompt)
        parsed_data = self._parse_json(raw_json)
        problems = self._story_problems(parsed_data, scene_count)
        if problems:
            logger.warning("Script needs another pass (%s) - asking the model to fix it.", "; ".join(problems))
            try:
                retry = await self._generate(
                    base_prompt + "\n        YOUR PREVIOUS ANSWER HAD THESE PROBLEMS - FIX THEM: " + "; ".join(problems) + "\n"
                )
                fixed = self._parse_json(retry)
                if not self._story_problems(fixed, scene_count) or len(fixed.get("scenes") or []) > len(parsed_data.get("scenes") or []):
                    parsed_data = fixed
            except Exception as e:  # noqa: BLE001
                logger.warning("Second script pass failed (%s) - keeping the first one.", e)

        if scene_count and len(parsed_data.get("scenes") or []) > int(scene_count):
            parsed_data["scenes"] = parsed_data["scenes"][: int(scene_count)]        # never more than asked for
        for i, sc in enumerate(parsed_data.get("scenes") or [], 1):
            if isinstance(sc, dict):
                sc["scene_number"] = i

        parsed_data = await self._complete_scenes(parsed_data, genre_hint, tone_hint, visual_style_hint, secs)

        parsed_data["story"] = story_text or None
        parsed_data["hook"] = (bible or {}).get("hook") or None
        parsed_data["story_outline"] = {k: v for k, v in (bible or {}).items() if k != "hook"} or None
        return ScriptDecompositionSchema(**parsed_data)