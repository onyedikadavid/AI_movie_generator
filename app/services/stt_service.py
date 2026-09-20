import os
from faster_whisper import WhisperModel

class STTService:
    def __init__(self, model_size: str = "base"):
        # CPU or GPU execution supported
        self.model = WhisperModel(model_size, device="auto", compute_type="default")

    def transcribe(self, audio_path: str) -> str:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
            
        segments, _ = self.model.transcribe(audio_path, beam_size=5)
        transcription = " ".join([segment.text for segment in segments])
        return transcription.strip()