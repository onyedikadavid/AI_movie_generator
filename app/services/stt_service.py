import os
import logging

logger = logging.getLogger(__name__)


class STTService:
    """
    Lazily imports faster_whisper so this class - and anything that imports
    it, including app/tasks/pipeline_tasks.py at module load time - doesn't
    require torch/faster-whisper to even be installed unless a project
    actually uses audio input. Matters a lot on a lightweight deployment
    (e.g. Render's free tier) where those packages won't fit at all.
    """

    def __init__(self, model_size: str = "base"):
        from faster_whisper import WhisperModel  # noqa: local import, see docstring
        self.model = WhisperModel(model_size, device="auto", compute_type="default")

    def transcribe(self, audio_path: str) -> str:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        segments, _ = self.model.transcribe(audio_path, beam_size=5)
        transcription = " ".join([segment.text for segment in segments])
        return transcription.strip()
