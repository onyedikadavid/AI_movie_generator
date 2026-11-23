## v2.35.3 - fix: "'_Img' object has no attribute 'cutout'"

- Drawing the bible pictures stopped after the first character: the helper that runs image-server calls (so they can be paused / stopped) only knew `generate_image`, not the new `cutout` and `compose_scene` calls. It now forwards every method by name, so this cannot happen again for a future call. (It also affected the scene pictures, which use `compose_scene`.)

## v2.35.2 - script rescue, reference-server auto-detect, notebook progress

- **Script breakdown no longer fails** when the writer model answers with only a list of scenes (screenplay-style keys, no title, no characters: "2 validation errors for ScriptDecompositionSchema"). The missing title / genre / tone / style come from your hints, the scenes are completed from the story, and the characters are taken from the scenes and the people who speak in the story, then described by one small extra call (simple entries if that call fails). If the model returned no scenes at all, your story is cut into scenes.
- **The Wan VACE notebook is detected automatically** (`/health` says `references: true`), so `VIDEO_REFERENCES=true` is no longer needed with it. Before, the old code sent a plain request and the server answered HTTP 400.
- **Notebook progress:** download heartbeat every 30 s with model GB on disk, a window/step line for every denoising step, `progress` on `/health` and on the job status, and the backend logs the server's progress once a minute.

## v2.35.1 - voices: Kokoro (local) and the newer Microsoft multilingual voices

- **`TTS_ENGINE=kokoro`**: Kokoro-82M runs on the PC that runs the worker (CPU, no notebook). `pip install -U kokoro-onnx` (Python 3.10-3.13); the two model files (~330 MB) download once into `storage/models/kokoro`. Each character gets a stable Kokoro voice (American a*, British b*); the narrator is `bm_fable`. Age and mood change speed, pitch and volume. Kokoro has no children's voices and no Nigerian accent. If it fails the run stops with a clear message and Resume continues (or set `TTS_ALLOW_FALLBACK=true` to use edge for that line).
- **edge-tts** now puts Microsoft's newer **multilingual** neural voices first (`en-US-AndrewMultilingualNeural`, `BrianMultilingualNeural`, `AvaMultilingualNeural`, `EmmaMultilingualNeural`) and Andrew narrates, instead of Christopher / Guy / Jenny (`TTS_EDGE_MULTILINGUAL=false` brings the old order back). Nigerian stories keep the Nigerian-accent voices first. If Microsoft refuses a voice name, the line falls back to Guy / Jenny and a warning is recorded.
- Voices already stored on characters belong to one engine: when you switch engine, a stored voice from the other engine is replaced. Characters that already have an edge voice keep it while the engine stays `edge`.

## v2.35.0 - the video model gets the bible pictures (reference images)

- **Why:** LTX / Ovi / MultiTalk only see ONE first picture and a text prompt, so they cannot "pick Michael from the character bible" - after the first seconds they invent faces again. A **reference-image model (Wan VACE)** is handed the bible pictures with the request and keeps them in view for the whole clip.
- **New notebook `wan_vace_reference_server_kaggle_v1.ipynb`.** Same `/v1/generate` API. It receives the **place picture first, then each character on screen** (speakers first, `VIDEO_REFERENCE_MAX=4`), builds the clip window by window (~5 s each) and gives **each window only the actions that fall inside it** (`window_beats`), so a 2-10 minute scene does not try to do everything in every window.
- **Backend:** set `VIDEO_REFERENCES=true`. The clip is silent, so every line is spoken whole by the pipeline's voice at the time the prompt gave for its beat (no line can be cut off); the prompt shows the person talking calmly, not the quoted words. Changing a bible picture makes the scene render again (the reference pictures are part of the scene fingerprint).
- Old notebooks keep working with `VIDEO_REFERENCES=false` (default).
- Honest limits: not timed on a real GPU; a 2-minute scene is ~25 windows, a 10-minute scene ~120; the lips are not matched to the words; the approved scene picture is not used as the first frame in this mode (the references carry the place and people).
- Wording fix: "20 s" in the timed-actions note below was an internal limit for tiny "finish the line" clips only; every scene is 2-10 minutes and always gets timed actions.

## v2.34.1 - actions are timed

- In every scene clip each action + line now gets a time in the prompt ("At 0:28 - Sarah rushes to the crib ..., and Sarah says ...: ..."), spread by how long each takes, with the last ~12 % of the clip left quiet (`TIMED_BEATS=true`). Before, the beats were only "First / Then / Next", and a video model that is given many beats in one long clip performs the first few and blurs the rest. Celery has nothing to do with this: it only runs the job, and the actions were already in the prompt.
- Limits: the video model decides how closely it follows the times; a baby that "arches back and stretches arms up" is still the model's interpretation of the first picture.

## v2.34.0 - scene pictures are the EMPTY place + the real bible characters

- A scene picture is no longer drawn with people in it. The place is drawn EMPTY (once per place; every scene set in the same place gets the same picture), the characters come from the character bible (each drawn once, then cut out of its own picture), and the scene picture is the place with those cut-outs standing in it (a light redraw, `BIBLE_COMPOSE_REFINE=0.25`, blends the light). The video then starts from that picture, so the people in the video are the bible characters. The video model only ever sees that first picture and the text - that is the only way a character reaches it.
- Describing a scene's picture on the review screen now describes only its PLACE; the characters are always added from the bible.
- Scenes are never chained to the previous scene (each starts from its own picture; Michael can leave the palace and the next scene can be a battlefield). Only clips inside ONE scene carry on from each other when the video server's limit forces more than one clip.
- Needs the new SDXL notebook (`sdxl_image_server_v3_compose.ipynb`: /v1/cutout and /v1/compose-scene). With an older notebook the previous way is used (characters drawn into the scene).
- Scene length: the Kaggle LTX launch cell caps clips with `LTX_MAX_FRAMES` (600 = about 25 s). For 2 to 10 minutes set it to 2880 (2 min) ... 14400 (10 min), and expect very long renders on a T4.

## v2.33.1 - every line spoken (model voice), without making the long clip again

- With `AUDIO_MODE=model` the video model speaks the lines. A 2-10 minute clip is never generated a second time just because its last line was cut off (that would cost hours). Instead (`SPEECH_TAIL_PATCH=true`): the clip is cut back to the pause before the unfinished line, and only that line is made in a short extra clip that continues from that exact frame (same people, place and voices) and is joined on. If that short clip is cut off too, it is made again longer (`SPEECH_MAX_RETRIES`), and as a last resort the pipeline voice says that one line (`SPEECH_FALLBACK_TTS`).
- Limits: the check finds a line cut off at the END of a clip. A line the model silently skips in the middle cannot be detected without speech-to-text (not added yet). The cut needs a clear pause before the last line; if there is none, the log says so.

## v2.33.0 - keep my story, chat with the writer, music bed, no character limit

- **Keep my story exactly** (new-project checkbox, on by default, `projects.keep_story`): the writer no longer rewrites your story. It only cuts it into scenes in order and adds what the camera needs; spoken words in your story are used as written.
- **Chat with the writer** (`POST /api/v1/chat`, page `/chat`): talk the story over with the Ollama model. It is told never to change your story unless you ask. "Use this as my story" fills the new-project page.
- **Music bed** (`MUSIC_BED=true`): put your own tracks (mp3/wav/m4a/ogg) in `storage/music/` (or set `MUSIC_DIR`). One track, picked by a word of the tone/genre in its file name (e.g. `sad_piano.mp3`), is looped under the whole film, faded in and out, and lowered while people speak (`MUSIC_VOLUME=0.18`, `MUSIC_DUCK=true`). No tracks = no music, nothing else changes.
- **Characters follow the story**: every character the story has is drawn (`BIBLE_MAX_CHARACTERS=0` = no limit). One picture can only follow a few faces, so when a scene has more people, the speakers (up to `BIBLE_REF_MAX=4`) are shown to the image model and the rest (a crowd) are drawn from the text.

## v2.32.0 - the cast and the places are drawn first (consistency)

- When you press "Generate scene pictures", the app now draws **every character** (one picture each: name, gender, age, outfit) and **every place** (an empty establishing picture) BEFORE any scene picture. They appear on the review screen; redraw any of them, optionally with your own description (it becomes the saved look). The scene pictures that show a changed character/place can then be redrawn in one click.
- Every scene picture is built from those pictures: the people in the scene are given to the image model as references (IP-Adapter, up to `BIBLE_REF_MAX=4`), and the picture starts from its place's picture (`BIBLE_PLACE_STRENGTH`). The video then starts from the approved scene picture.
- **Update the SDXL notebook** (`sdxl_image_server_v2_characters.ipynb`). An old SDXL notebook still works (it ignores the references), but then nothing keeps the faces the same. Check `/health` shows `"ip_adapter": true`.
- Settings: `BIBLE_PICTURES=true`, `BIBLE_MAX_CHARACTERS=0` (no limit), `BIBLE_REF_MAX=4`, `BIBLE_REF_SCALE=0.7`, `BIBLE_PLACE_STRENGTH=0.72`.
- Not covered: "straight to video" projects (no script, so no named characters), and faces can still drift in the middle of a very long clip - only its first frame is pinned.

## v2.31.0 - long scenes (2-10 min), pictures first, calmer film

- **One scene = one clip, 2 to 10 minutes.** No seconds anywhere in the app; the length is chosen in minutes (2-10). No shots, no splitting, no film chaining (`WHOLE_ONLY=true`, `WHOLE_SCENE_STRICT=true`). A scene is only continued in a second clip if your video server's `/health` reports a ceiling below the length asked for.
- Set `VIDEO_MAX_CLIP_SECONDS=600`, `VIDEO_SERVER_TIMEOUT=21600`, `VIDEO_JOB_MAX_WAIT=43200`.
- **Pictures first.** Script -> "Generate scene pictures" -> review (redraw one with your own words, or reuse another scene's picture) -> "Make the video". Approved pictures are kept in `storage/projects/<id>/images/`, and each scene's video starts from its picture. New status `IMAGES_READY`, new column `projects.images_approved` (added automatically; alembic `c5e2a8b97f13`).
- **Less rush** (`CALM_STAGING=true`): people only walk when the scene says so, one steady camera, unhurried pace, dialogue spread with pauses, the last line finishes well before the end. `CLIP_TRIM_HEAD_SECONDS`/`CLIP_TRIM_TAIL_SECONDS` trim the model's first and last moments.
- Not done yet: keeping your story word-for-word, the writer chat page, a continuous music bed.

## One clip per scene (SCENE_MODE=whole)

    AUDIO_MODE=model
    SCENE_MODE=whole
    WHOLE_SCENE_MAX_SECONDS=8

Each scene is ONE generation: one prompt with the place, cast, every action and quoted line in order, and a moving camera.
- The backend asks the Kaggle server for its real limit (`max_frames` on /health) and never asks for more. Set
  `LTX_MAX_FRAMES` in the Kaggle launch cell: 121 = ~5 s, 241 = ~10 s, 361 = ~15 s (needs the v4 notebook).
- If the dialogue is longer than the clip can hold, the lines are shortened (a warning in the worker log says so).
  `WHOLE_SCENE_SPLIT=true` allows splitting a long scene between lines instead.
- A clip the server returned shorter than asked (GPU memory) is kept; `WHOLE_SCENE_FALLBACK=true` redoes it shot by shot.
- Finished clips are cached per exact prompt (`whole_N_raw.mp4.key`).

## Clip length (why scenes were ~2 s)

Clip length is set by the Kaggle server, not the backend: `LTX_MAX_FRAMES` in the launch cell (24 fps): 121 = 5 s,
145 = 6 s, 193 = 8 s. A server started with a small value (your log showed 65 frames = 2.7 s) makes every scene that
short and squeezes the dialogue, which sounds rushed. Set it to 193 and keep `WHOLE_SCENE_MAX_SECONDS=8`. Longer clips
need more GPU memory and time; if the server runs out of memory it retries shorter (the worker log says so), and
`VIDEO_T2V_RESOLUTION=768x448` gives it more room.

## Script writer

The full story (hook, structure) is saved with the script and shown in the app ("The story" card on the review and player screens).

The prompt is first analysed (logline, main character, problem, turning point, ending, message, hook). The scenes are
then written from that analysis: scene 1 is a hook, the middle escalates, the last scene resolves, and the story stays
faithful to the prompt. A script with fewer than 3 scenes or no hook/ending is sent back once for a fix.

## Scene count, scene length, downloads (set in the app)

New Project screen: *Number of scenes* (empty = the script writer decides, usually 3-8 depending on the story) and
*Seconds per scene* (empty = `WHOLE_SCENE_MAX_SECONDS`). Seconds can be changed later on the review screen. The video
server's own maximum (`LTX_MAX_FRAMES`) still applies. Text-to-video never draws images; only "Image first" does.
Every finished scene is uploaded on its own (Scenes list on the player page, Download button).

## Continue the story

Player page -> *Continue the story*: describe what happens next (or leave it empty), pick how many new scenes, press the
button. The writer gets the story so far, the scenes so far and every character with their exact look, and adds new
scenes after the last one (new characters only if the story needs them). Finished scenes and existing characters are
never changed; the project goes back to review, and *Generate* renders only the new scenes, then re-joins the film.
Consistency: character looks and voices are reused verbatim. A new scene marked *Carry on from the previous scene*
starts from the previous clip's last picture (works in text-to-video and image-first mode, needs SCENE_MODE=whole),
which keeps people and place identical. A scene that is a normal cut starts fresh from text/keyframe, so a face can
still drift a little - true face locking needs a reference-image model (IP-Adapter) in the image notebook.

## Narrator on/off

Now a per-project switch (New Project screen and the review screen). `NARRATOR_ENABLED` in .env is only the default
for projects that never touched it. Switching it re-renders the scenes on the next run.

## v2.13: complete spoken lines, real conversations, audio-driven video models

**Spoken lines were being cut off.** Three causes, three fixes:
- The clip length was a guess (2.5 words/s, capped at 5 s). Now `MODEL_VOICE_WPS=2.1`, a bigger safety margin, and `VIDEO_MAX_CLIP_SECONDS=8` / `MODEL_VOICE_MAX_WORDS=13` by default (lines are split less often and less mid-sentence).
- After every model-voice clip the backend listens to the last 0.3 s. A voice still going at the very end means the line was cut: the clip is generated again, longer (`SPEECH_COMPLETE_CHECK`, `SPEECH_RETRY_EXTRA_SECONDS`, `SPEECH_MAX_RETRIES`). This is a loudness heuristic on the clip's own audio, not speech recognition; tune `SPEECH_TAIL_MARGIN_DB` if it fires too often or too rarely. If the server cannot make a longer clip, the backend logs a warning and keeps the clip.
- The LTX notebook used to shorten clips silently when the GPU ran out of memory and to round the length down. `ltx23_wan2gp_api_server_kaggle_v5.ipynb` fails with a clear message instead (`LTX_STRICT_LENGTH=1`) and rounds up.

**Characters walked around while talking.** `VIDEO_CAMERA_MOTION=auto` (new default) chooses the camera shot by shot: a conversation gets a calm camera and characters who stay in place and face each other (over-the-shoulder, the listener visible in the foreground, "no walking, no pacing"); an action beat (a fall, a chase, a fight, an explosion...) or an establishing shot gets a dynamic camera. Walking/pacing/running in a dialogue action is dropped unless the shot is an action beat. `model`, `gentle` and `static` still work exactly as before.

**Audio-driven video models** (WanGP MultiTalk / InfiniteTalk / Hunyuan Avatar) do not make a voice: they animate a face to an audio file. Set `AUDIO_MODE=voices`, `VIDEO_AUDIO_DRIVEN=true` and use an image-first project. Each shot is sent its own slice of the scene's voice track and the clip is exactly as long as the slice, so words cannot be cut off. The final sound is still the pipeline's own voices. Models that make their own voices (LTX-2.3, Wan 2.2 Ovi, MiniMax H3) use `AUDIO_MODE=model`.

New self-test: `python scripts/selftest_dialogue_and_speech.py`. (Changing these settings changes the scene fingerprint, so existing scenes re-render once.)

## Model voices, free camera, and the image-vs-text switch

**Voices.** `AUDIO_MODE=model` (the default) makes the video model (LTX-2.3) speak the lines itself - no edge-tts. Each line is written
into the clip's prompt as `Kobi says in an adult male voice with a Nigerian accent: "..."`, with the character's gender, age and
accent taken from the Cast screen so a voice stays similar between shots. Every spoken line is its own clip (long lines are split at
sentence ends, `MODEL_VOICE_MAX_WORDS=10`) and clips keep their natural length, so lips stay in sync. Works in both modes (text-to-video
and image-first). To go back to edge-tts: `AUDIO_MODE=voices` (or `hybrid`).

**Action vs. words.** A dialogue line may hold both, e.g. `Irritated Tapping on the mixing board... "What's with this distortion?"`.
Only the words are ever spoken; the stage direction goes into the turn's **Action** and is acted out. This happens when the script is
written, when you save a scene, and again at render time (so scripts written before this update are fixed too).

**Camera.** `VIDEO_CAMERA_MOTION=model` (the default) lets the camera move like a normal film: the scene's *Motion / camera direction*
is used on the first shot, then a rotating set of classic moves (push-in, tracking, handheld, arc, dolly). `gentle` = small moves only,
`static` = the old locked-off tripod look. Speakers are framed waist-up so hands and body can act.

**Image first vs. text-to-video, per project.** Pick it on *New project* or on the review screen (`PATCH /api/v1/projects/{id}`
with `{"video_mode": "i2v" | "t2v"}`). `VIDEO_MODE` in `.env` is only the default. Switching re-renders the scenes on the next run.

Run `python scripts/selftest_model_voice.py` (needs only ffmpeg + Pillow) to check all of this without a GPU.
New database column `projects.video_mode` is added automatically on start-up (or `alembic upgrade head`).

---

## Text-to-video mode (LTX-2.3 via WanGP)

Set `VIDEO_MODE=t2v` in `.env` (default is `i2v`). In this mode no keyframes are drawn (the SDXL notebook is not needed);
each shot is generated from a text prompt that repeats every character's appearance, and a thumbnail is cut from the first
clip for the gallery. Voices (edge-tts) are still added by the pipeline.

```
VIDEO_MODE=t2v
VIDEO_T2V_RESOLUTION=960x544   # both numbers divisible by 32
VIDEO_SERVER_TIMEOUT=3600
VIDEO_SHOT_SECONDS=4.5
VIDEO_MAX_CLIP_SECONDS=5.0
```

**Sound (hybrid):** `AUDIO_MODE=hybrid` keeps the pipeline's character/narrator voices and also mixes in the video
model's own background sound and effects (wind, crowds, gunfire, footsteps...), automatically lowered by ~11 dB while someone speaks
and crossfaded between shots. Each scene has an editable "Background sound & effects" field (the script writer fills it in). Prompts tell the
model "no spoken dialogue, no music" so it never adds voices of its own. `AUDIO_MODE=voices` turns the model's sound off.
Only text-to-video with an audio-capable model (LTX-2.x) produces background sound; otherwise the clip is silent and only the voices play.

```
AUDIO_MODE=model         # model (default) | hybrid | voices
AMBIENCE_VOLUME=0.55     # background loudness before ducking
```

**Credit / terms:** video generation in this mode uses [WanGP (Wan2GP)](https://github.com/deepbeepmeep/Wan2GP). Its terms require
that products integrating its API say so clearly in their interface and documentation - the web app footer does, and so does this note.
Check the WanGP and LTX-2 license terms before any commercial use.

---

## Video jobs (long renders)

Video clips are requested as background **jobs**: the backend submits the clip, the GPU server answers at once with a job id, and the backend
polls until it is done and then downloads it. A dropped ngrok connection or a timeout can no longer fail a render that is still running, and a
retry (or Resume) re-uses the same job instead of making the GPU render the clip twice. Needs the `v3-jobs` Kaggle server notebook; older servers
still work (the backend falls back to waiting for the video in one request).

```
VIDEO_ASYNC=true            # false = old single-request mode
VIDEO_POLL_SECONDS=6
VIDEO_JOB_MAX_WAIT=7200     # longest wait for ONE clip, in seconds
```
Self-test: `python scripts/selftest_video_client.py`

**Long waits and the database:** before every long wait (GPU server, LLM, upload) the worker saves its work and hands its database
connection back, and error handling tolerates a connection the hosted database dropped - so a multi-minute render can no longer end in
`SSL connection has been closed unexpectedly` and hide the real error. Self-test: `python scripts/selftest_db_safety.py`

## Narrator

`NARRATOR_ENABLED=false` (default): **only the characters speak** - no narrator voice, narration text is not read aloud, and any line the script labels
"Narrator" is skipped. A character who is alone in a scene is given spoken lines of their own by the script writer, so those scenes aren't silent.
Set `NARRATOR_ENABLED=true` to bring the narrator voice back. Projects whose script was written earlier keep their existing dialogue: a lone-character scene with no
dialogue stays silent until you add a line in the scene editor ("Add a spoken line") or write the script again.

## Style options on the New Project form

* **Visual style -> Cinematic:** film-style prompts (anamorphic lens, dramatic motivated lighting, shallow depth of field, colour grading, film grain) and
  cinematographer-style scene prompts from the script writer.
* **Genre -> Kids / Family** (or **Visual style -> Kids cartoon**): the script is written for ages 3-8 - simple words, gentle story, no violence/scary/adult
  content, happy ending - dialogue agents are told the same, and images/videos use a bright children's-cartoon look with a "no scary / violent / realistic" negative prompt.
  Picking the Kids genre selects the Kids look automatically. (An AI model can still slip - review the script before generating for children.)
* Your own choice always wins over the script writer's paraphrase of it.

# v2 - what changed, and how to run it

See `CHANGES.md` (one level up, next to this folder) for the full explanation. Short version:

**Start order (every time)**
1. Run the notebooks you need (Ollama -> script; SDXL + LTX -> pictures/video). Re-run them from the top: they were updated.
2. `pip install -r requirements.txt` (adds `edge-tts`, the natural-voice engine).
3. Start the API (`uvicorn main:app ...`). On start it upgrades the database schema by itself.
4. Start **one** worker (Windows: add `--pool=solo`):
   `celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo`
   Never run a second worker on another machine at the same time.

**Optional `.env` settings** (all have sensible defaults)
```
TTS_REGION=auto            # auto | ng | us | gb  - accent family for the voices
TTS_NARRATOR_VOICE=        # e.g. en-GB-RyanNeural
TTS_ALLOW_FALLBACK=false   # true = use the old robotic voice instead of failing when neural voices are unreachable
SHOT_COVERAGE=true         # close-up keyframe per speaker (sharper faces, slower)
VIDEO_SHOT_SECONDS=4.5
VIDEO_MAX_SHOTS_PER_SCENE=5
OUTPUT_WIDTH=1280
OUTPUT_HEIGHT=720
STALE_RUN_SECONDS=90       # how long a silent worker is trusted before its project is marked dead
```

**Self-test (no GPU, no network needed):** `python scripts/selftest_renderer.py`

---

# AI Story-to-Video Engine — Backend

FastAPI + Celery backend for the multi-character AI video production pipeline.

## Media storage when the worker and API are on different machines

If your Celery worker runs on your PC but your API is on Render, generated
keyframes and the final video are written to the *worker's* disk, while the
browser fetches media from the *API* - which doesn't have those files, so
images show as broken and the finished video won't play. Fix: set either the `S3_*` variables in `.env` (see `.env.example`) to any S3-compatible bucket with public read access (Cloudflare R2 has a free tier; `S3_PUBLIC_BASE_URL` is the bucket's public URL), or the three `CLOUDINARY_*` variables instead (also free, simpler setup - just an account, no bucket/public-access configuration). If both are set, Cloudinary takes priority. After each keyframe and the final video are
produced, the worker uploads them and stores the public URL in the database;
the frontend uses full URLs as-is. Leave the `S3_*` values blank if the API
and worker share a filesystem (e.g. both run on your own machine). Uploads
never abort a run - on failure it logs a warning and keeps the local path.
Note: deleting a project removes local files only, not uploaded ones.

## Never hand-edit a Colab URL into .env/Render again

Every notebook (`ltx_video_server.ipynb`, `sdxl_image_server.ipynb`,
`asset_generation_server.ipynb`, `ollama_server.ipynb`) now has an optional
cell, right after it opens its ngrok tunnel, that publishes the current URL
to your Upstash Redis via its REST API. Set `UPSTASH_REDIS_REST_URL` and
`UPSTASH_REDIS_REST_TOKEN` in `.env` (from your Upstash console's **REST
API** tab - a separate thing from the `rediss://` connection string Celery
uses), and `LLMService`/`ImageGenerationService`/`VideoGenerationService`
will look up the *live* URL on every single call instead of trusting a
static env var. Restart a notebook, run its publish cell (paste the same
two values when it prompts), and the backend picks up the new URL on its
very next request - no `.env` edit, no Render redeploy, no backend restart.

This is opt-in and fails soft: leave those two values blank and everything
behaves exactly as before, reading only the static `OLLAMA_URL` /
`IMAGE_API_URL` / `WAN_API_URL` values. If Upstash's REST API is ever
unreachable, the lookup silently falls back to those same static values
rather than failing the request - a flaky convenience lookup should never
be why generation breaks.

## If you already have a live database (Neon or otherwise)

Run `alembic upgrade head` again after pulling this update - it adds a new
migration (`cancel_requested`/`celery_task_id` columns, a `CANCELLED`
status) on top of your existing schema. It won't touch existing data.

Also **critical, not optional**: `app/core/celery_app.py` was missing
`include=["app.tasks.pipeline_tasks"]`, which meant the worker process
never actually registered any tasks - every project created before this
fix was silently discarded (`Received unregistered task ... KeyError`
in the worker log). Restart your worker with the updated code.

## What changed in this pass (backend audit)

The codebase was reviewed end-to-end against the two PRDs and the frontend
that now consumes it. Fixes made:

1. **Alembic migration was completely out of sync with the models**
   (wrong column names, and the entire `scripts` table was missing). This is
   why `init_db.py` / `force_create.py` existed as a `create_all()`
   workaround. The migration in `alembic/versions/` has been rewritten to
   match `app/models/*.py` exactly.
2. **Two-phase pipeline.** Previously, creating a project immediately ran
   the *entire* pipeline (script → images → video → composite) with no
   chance to review or edit anything, contradicting the PRD's own flow
   ("Scene & Asset Review" happens *before* "Asset & Video Generation").
   The Celery task is now split:
   - `run_script_breakdown` — STT + LLM parsing only. Runs automatically on
     project creation. Leaves the project in `SCRIPT_READY`.
   - `run_asset_pipeline` — image/video/audio generation + composition.
     Triggered explicitly via `POST /api/v1/projects/{id}/run-pipeline`,
     matching the PRD's documented endpoint. Reads scenes back out of the
     database (not the original LLM output), so edits made during review
     are respected.
   - Added `Scene.dialogue_turns` (JSON column) so multi-character dialogue
     generated in Phase 1 survives into Phase 2.
3. **Missing endpoints the frontend needs** — added `GET /projects` (list,
   for the dashboard), a richer `GET /projects/{id}` (script + characters +
   scenes, for the review/player views), `PATCH .../scenes/{id}` and
   `PATCH .../characters/{id}` (human-in-the-loop editing before
   generation), and `DELETE /projects/{id}`.
4. **`/health` endpoint** was an empty file; implemented a real check of
   DB/Redis/Ollama reachability, which the frontend polls for a status
   indicator.
5. **`PipelineOrchestrator`** referenced `ProjectStatus.IN_PROGRESS` /
   `PENDING`, which don't exist on the model (dead code that would raise
   `AttributeError` the moment it ran) — fixed to use real statuses and the
   two real tasks.
6. **Shell-injection risk in `TTSService`** — it built a shell string with
   raw LLM-generated text inside `subprocess.run(..., shell=True)`. Switched
   to argument-list `subprocess.run` with text piped via stdin.
7. `VideoGenerationService`'s default `WAN_API_URL` pointed at
   `localhost:8000`, the same port the FastAPI app itself listens on.
   Changed the default to `8188` (ComfyUI's default) so a misconfigured env
   doesn't silently loop back into this API.
8. Static file mount (`/storage`) added to `main.py` so the frontend can
   render generated keyframes/videos directly by URL.
9. CORS origins are now configurable via `CORS_ORIGINS` instead of hardcoded
   `*`.
10. `requirements.txt`: added `Pillow` (used directly by `SpatialService`
    but never declared), and clarified that `piper` (TTS) and `controlnet_aux`
    (pose maps) are external/optional installs, not pip packages that
    `pip install -r requirements.txt` alone will fully satisfy.
11. `VideoGenerationService` sent the video backend a **local file path**,
    which only works if the video model runs on the exact same machine as
    this API. Rewrote it to upload the actual image bytes over HTTP instead,
    so a remote GPU (e.g. the Colab notebook in `../notebooks/`) can receive
    the keyframe. Also changed it to raise a clear error (failing the
    project with a real message) instead of silently continuing when the
    video backend is unreachable or errors.

## Local setup

```bash
cp .env.example .env          # adjust if your ports/creds differ
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Postgres + Redis (or run these two via `docker compose up db redis`)
alembic upgrade head

# Terminal 1
uvicorn main:app --reload

# Terminal 2
celery -A app.core.celery_app.celery_app worker --loglevel=info
# On Windows, add --pool=solo: python -m celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo
# Celery's default "prefork" pool relies on Unix fork(), which Windows
# doesn't have - it's officially Celery's own recommendation to use the
# solo pool on Windows instead, and it also sidesteps some flaky
# reconnect-after-idle behavior that shows up more on Windows.

# Terminal 3 - local LLM
ollama serve
ollama pull llama3
```

Then run the frontend (see `../movie-pipeline-frontend/README.md`) against
`http://localhost:8000`.

### Optional local generation backends

- **Images**: `ImageGenerationService` loads SDXL via `diffusers` on first
  use if `IMAGE_API_URL` is unset (needs a GPU with enough VRAM to be
  practical; downloads ~7GB of weights to this machine the first time).
  Set `IMAGE_API_URL` to skip local generation entirely and call a remote
  server instead - `../notebooks/asset_generation_server.ipynb` runs SDXL
  *and* the video model together on one free Colab/Kaggle GPU behind one
  ngrok tunnel, so nothing downloads locally.
- **Video**: `VideoGenerationService` posts the keyframe image (as file bytes,
  not a path - see below) to `WAN_API_URL`. If unreachable or it errors, the
  project fails with a clear message rather than silently producing a scene
  with a missing clip.
  - **No video model of your own?** `../notebooks/ltx_video_server.ipynb`
    runs a free-tier-friendly open video model (LTX-Video) on a free Google
    Colab GPU and exposes it over an ngrok tunnel - paste the URL it prints
    into `WAN_API_URL`. Kaggle notebooks work the same way (enable
    "Internet" in notebook settings, run the same install/serve cells,
    ngrok works there too) if you'd rather use Kaggle's free 30 GPU-hrs/week
    instead of Colab's session limits.
  - **Be realistic about output quality.** This gets you real generated
    motion per shot instead of a still image panned around - not
    theatrical-film quality. That ceiling belongs to the model
    (LTX-Video/Wan/etc.), not this codebase; there's no configuration that
    changes it.
- **TTS**: install the `piper` CLI binary and a voice model
  (see https://github.com/rhasspy/piper) for real narration; otherwise a
  silent placeholder clip is generated so the pipeline keeps moving.

## No Docker (e.g. Windows without virtualization enabled)

Docker Desktop needs hardware virtualization (WSL2 or Hyper-V) - if your
machine can't enable that, skip Docker entirely and use free hosted
services instead of local Postgres/Redis:

- **Postgres**: [Neon](https://neon.tech) (free tier, no card required).
  Copy its connection string into `DATABASE_URL_OVERRIDE` in `.env` - see
  the comment above that field for the exact format asyncpg expects.
- **Redis**: [Upstash](https://upstash.com) (free tier, serverless Redis).
  Copy its `rediss://` connection string into `REDIS_URL_OVERRIDE`.

With both set, `alembic upgrade head`, the Celery worker, and `uvicorn`
all work exactly the same as with local/Dockerized services - nothing else
in the setup changes.

## Deploying to Render (or Railway/Fly/similar)

Three things that are easy to miss and will make the deployed API
completely unreachable or silently broken even though it "deployed
successfully":

1. **`.env` never reaches the server.** It's gitignored (correctly - it can
   hold real secrets), which means if you deploy from a git repo, Render
   never sees it at all. Every variable you want in production -
   `DATABASE_URL_OVERRIDE`, `REDIS_URL_OVERRIDE`, `CORS_ORIGINS`,
   `OLLAMA_URL`, `IMAGE_API_URL`, `WAN_API_URL` - has to be re-entered
   directly in Render's dashboard under the service's **Environment**
   tab, separately from your local `.env`.
2. **`CORS_ORIGINS` must list your actual deployed frontend URL**, not just
   `localhost`. If it doesn't, the browser blocks every response with a
   CORS error that surfaces to the user as a generic "Failed to fetch" -
   indistinguishable, from the frontend's point of view, from the backend
   being down entirely. Set it to something like:
   `CORS_ORIGINS=https://your-frontend.vercel.app`
   (comma-separate multiple origins; no trailing slash on any of them).
3. **The port Render routes traffic to is dynamic**, injected as the
   `$PORT` env var - it is *not* 8000. The `Dockerfile` in this repo reads
   it correctly (`--port ${PORT:-8000}`, falling back to 8000 only when
   `$PORT` isn't set, e.g. local `docker run`). If you ever hardcode a
   port in a Start Command instead of using this Dockerfile as-is, use
   `--port $PORT`, not a fixed number - a hardcoded port is the single
   most common reason a Render web service builds fine but is completely
   unreachable from outside.

You'll also need a **second** Render service (type: Background Worker, not
Web Service) running the Celery worker command below, pointed at the same
repo and given the *same* environment variables as the API service -
otherwise projects get created but never actually process, since nothing
is consuming the queue:
```
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

This repo ships two requirements files:
- `requirements-render.txt` - what the Dockerfile actually installs.
  Lightweight; assumes Ollama/image/video generation happen remotely
  (Colab/Kaggle notebooks) rather than in-process.
- `requirements.txt` - the full list, including `torch`/`diffusers`/
  `faster-whisper`, for running everything locally on your own GPU
  machine instead (no Docker needed there - just `pip install` directly).

## Docker

`docker-compose.yml` brings up Postgres, Redis, the API, and a Celery
worker. The worker's GPU reservation block requires the NVIDIA Container
Toolkit; if you're on a CPU-only machine, delete that `deploy:` block from
the `celery_worker` service before running `docker compose up`.

## v2.26: the scene decides how people move (CONVERSATION_STAGING)

Characters that bounced up and down, ran around while saying "I'm stuck", or stood in place without talking to each other came
from the prompt, not the video model. Now WHAT IS HAPPENING decides how a character is staged, in this order:

1. **A situation** named in the shot's action, the spoken line, the scene description or the camera note is shown with the
   body, for the whole scene: *stuck / trapped / sinking* (struggles and pulls in the same spot, never walks or runs; the other
   character stays beside them and helps), *lying hurt*, *swimming*, *climbing*, *hiding*, *sitting*. "Keto is stuck in mud"
   or Keto saying "I'm stuck" is enough. The camera follows (a slight handheld shake for a struggle).
2. **A specific action** ("kneels and opens the old box", "grips the rope") is performed exactly as written.
3. **Only when nothing specific is happening** the conversation is staged by `CONVERSATION_STAGING` (in `.env`, used with
   `VIDEO_CAMERA_MOTION=auto`): `walk` (default) = they walk side by side at an easy pace, turn to each other, gesture, react,
   no bouncing / jumping / pacing, camera tracks them; `stay` = they stand and face each other; `free` = the model decides.

Action beats (falls, chases, fights) keep their dynamic camera. The same rules apply in all four "How each scene is built"
styles (the whole-scene prompt shows the situation in every beat). The script writer is told to give every line a specific
action that fits the situation, and to state any predicament plainly in the scene description.
This is prompt-level guidance: the video model still decides the final motion, so a clip can occasionally ignore it. If it
does, write the situation into the scene description in the editor (e.g. "Keto is stuck waist-deep in thick mud") and re-render.

Audio-driven mode (`VIDEO_AUDIO_DRIVEN=true`, MultiTalk / InfiniteTalk / Hunyuan Avatar) works in "Shot by shot" only.
The Draft/Fast/Balanced/High picker is ignored by a notebook that sets `FORCE_RESOLUTION` (Ovi / H3 / MultiTalk / Hunyuan).

## v2.28: spoken lines are never cut off

What went wrong: in "Whole scene" mode a scene whose dialogue did not fit one clip had every line shortened to its first few
words ("this fruit is gotten from an apple tree" became "this fruit is gotten from."), and a whole-scene clip was never checked
for a cut-off line (only shot-by-shot clips were). The length estimate was also so padded that one clip held only ~6 words.

Now:
1. **Nothing is shortened or cut mid-sentence.** A scene that does not fit one clip is split into more clips (lines in order,
   each piece ending at a sentence / clause end). A later piece carries on from the last picture of the one before it.
2. **Every clip is checked** (whole-scene and shot-by-shot): if the voice is still going at the clip's very end it is generated
   again, longer (up to the video server's real limit, read from `/health`; `SPEECH_MAX_RETRIES`).
3. **Still cut off?** The scene is split so that line gets a clip of its own; a single long line is cut into two complete pieces.
4. **Last resort** (`SPEECH_FALLBACK_TTS=true`): the pipeline's own voice says the whole line over the clip (the picture holds on
   its last frame if needed). Lips will not match for that line, but the meaning is never lost.
5. Clip length and words per clip follow the real server limit: `min(VIDEO_MAX_CLIP_SECONDS, LTX_MAX_FRAMES / 24)`. If your
   `.env` says 10 s but the notebook is at `LTX_MAX_FRAMES=193` (8 s), 8 s is used. For longer clips raise `LTX_MAX_FRAMES`
   in the Kaggle launch cell (241 = 10 s; needs more GPU memory and time).
A slow-speaking voice means more clips / retries, so a scene can take longer to render - that is the price of complete sentences.

## v2.29: how a line is said, and the story bible

**Delivery (volume, pitch, pace, voice quality).** `app/services/delivery.py` works out HOW each line is said from the
speaker's emotion, what is physically happening (stuck, hiding, swimming, climbing...) and the line itself (`!`, `?`,
`...`, CAPS, "help", "look out"). It is written into the video prompt ("says in a high-pitched, quick, shaky, trembling
voice") so the video model's own voice performs it, and it drives rate / pitch / volume for the fallback TTS voices. The
dialogue writer now asks each character for *how it is said* (whispering, shouting harshly, soft and sad...) and for an
Action that happens WHILE the words are spoken and matches them.

**Story bible (`app/services/bible.py`).** Every project gets one entry per place and per character:

* *Places* (`locations` table): each scene's free-text `location` is matched to ONE place however it was worded
  ("Jack's carpenter workshop", "Carpenter Workshop - morning" -> one place; "Mechanic workshop" stays separate). Each place
  has one saved description (layout, props, colours, light - no people) used in every scene set there, and the keyframe
  seed is per place, so the room comes back the same.
* *Characters*: `core_look` (face, hair, skin, build - never changes) plus `outfit` and `outfits` per place, so Jack is the
  same man in a leather apron in the carpenter's workshop and in overalls in the mechanic's.
* Built after the script is written and again before generation / after "Continue the story"; **only missing entries are
  created** - existing or edited ones are never rewritten, and "Continue the story" is told the existing places so new
  scenes reuse them by name.
* Edit on the review screen (Places section, character cards) or via `PATCH /projects/{id}/locations/{id}`,
  `POST /projects/{id}/locations/merge`, `PATCH /projects/{id}/characters/{id}` (`core_look`, `outfit`, `outfits`).
  Editing a place or look re-renders only the scenes that use it.
* The schema upgrades itself on start (new `locations` table, new columns); an Alembic migration is included too.
* Needs the Ollama notebook for the descriptions; without it the bible is built from the script's own text.

Limit: this keeps the *description*, seed and outfits identical, which makes places and faces much more consistent, but
the image/video models can still vary details - it is not a pixel-exact reference image.

## v2.29.2: story text shown as plain text

A model that answered the story with an object (`{"hook": "...the whole story..."}`) used to be saved as a printed Python
dict, so the review screen showed `{'hook': ...}` with literal `\r\n`. `app/services/text_clean.py` now flattens objects,
lists and printed dicts into plain paragraphs when the story is written, when it is continued, when it is read back for
prompts, and when the script is sent to the app - so projects saved before this fix display correctly too.

## v2.29.3: descriptions are never cut mid-thought

`clip_words` (used to fit a place, a look, a setting or a camera note into the video prompt) used to cut at a word count, which
could leave "...flickering torches cast eerie". It now ends at the last full sentence, else the last clause, that fits.
The Kaggle notebooks printed only the first 160 characters of each prompt in their log (the prompt sent to the model was
complete); the new notebooks print the whole prompt.


## v2.29.4: finished clips are never thrown away for nothing

Why a scene could be rendered again and again (about 3 GPU hours on one scene in a real log):
* Any change - even `VIDEO_MAX_CLIP_SECONDS` or a speech setting - wiped the scene folder, including finished clips.
  Now only a change to the PICTURE (scene look, characters, style, size, where it continues from) starts over. Every other
  change keeps the finished clips; each clip is reused only if its own request (prompt, length, seed, source) is unchanged.
* A clip whose last word seemed cut off was deleted and the scene split; a Resume then rendered the same too-big clip
  again, found out again, split again. The split is now remembered (`split_memory.json` in the scene folder).
* A clip that took longer than `SPEECH_RETRY_MAX_RENDER_SECONDS` (default 900 s) is kept: no retry, no split. On a T4
  one clip takes 25-40 min, so a repeat costs more than a slightly clipped last word.
* The log now prints the plan ("Scene 1 plan: clip 1: ...") and, per clip, "finished earlier - reusing it" or "generating".

Video server (LTX notebook v12): `DELETE /v1/jobs/{id}` drops a queued job (a running one cannot be interrupted);
a new request from a project drops that project's older queued jobs (they were replaced). The backend stops polling a job
when a run is paused / stopped and asks the server to drop it. A running job is left to finish, so Resume re-attaches to it.


## v2.30.0: "Straight to video" (no writer model / Ollama)

New project field `generation_mode` (form field on `POST /api/v1/projects`):

* `script` (default) - as before: the writer model writes a script you can review, then you generate.
* `direct` - no writer model at all. Your idea is the video prompt. The project is created, goes straight to the
  video model (text-to-video, whole-scene style) and starts rendering at once. No script review step, no story bible,
  no Ollama needed (the image/SDXL notebook is not needed either).

Length: `scene_seconds` is the clip length (the video server's own ceiling still applies); `scene_count` N > 1 makes
N clips in a row, each carrying on from the last picture of the one before. Describe the people, place, action and
camera in the idea; nobody speaks unless the video model is asked to (put the words in the idea in quotes).
"Continue the story" is not available on a direct project (it needs the writer model).

Database: new column `projects.generation_mode` (added automatically at start-up; alembic `a7d41c9e3b52`).

## v2.29.5: the camera follows the conversation (who is talking TO whom)

Problem: speakers faced the lens like news presenters (the camera straight in front of them), so you could not tell who
they were talking to. Cause: the whole-scene prompt only said "follow whoever is speaking, with natural cuts", the script's
own camera note was often "slow dolly-in on X's face", and nothing told the model where the listener was or where to look.

Now (`CONVERSATION_COVERAGE=true`, default):
* **Two or more people**: the prompt places them face to face, seen from the side in profile / three-quarter view, and
  directs the camera as a side-on two-shot, then over-the-shoulder shots (over B's shoulder onto A while A speaks, reversing
  when B answers). Each line says who it is said TO (a name inside the line, else the person who spoke before or after,
  else the only other person) and "eyes on" them. Nobody looks into the lens.
* **A lone speaker** (a crowd, someone off-frame): three-quarter view from beside the listeners, eyes off the lens.
* The face-on parts of the script's own camera note ("dolly-in on her face", "close-up") are dropped; the rest is kept.
* Keyframes and close-ups are drawn side-on / three-quarter with eyes turned away from the lens, and "looking at the camera"
  is added to the image negative prompt.
* A deliberate direct address ("to the camera", a vlog, a news report) keeps the old frontal wording.
* The script writer is told to plan conversations as side-on / over-the-shoulder coverage.
`CONVERSATION_COVERAGE=false` restores the old wording. This changes prompts and keyframes, so scenes already rendered are
made again once (their pictures are different). It guides the video model; it cannot force it.
