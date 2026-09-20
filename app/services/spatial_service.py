import os
import logging
from PIL import Image

logger = logging.getLogger(__name__)

class SpatialService:
    """
    Handles control signals (OpenPose skeletons, Depth maps) to guide dynamic character physics.
    """
    def __init__(self):
        self._pose_detector = None
        self._depth_detector = None

    def _init_detectors(self):
        # Lazy loading models to conserve local GPU memory
        if not self._pose_detector:
            try:
                from controlnet_aux import OpenposeDetector
                self._pose_detector = OpenposeDetector.from_pretrained("lllyasviel/ControlNet")
            except Exception as e:
                logger.warning(f"Could not load OpenPose detector: {e}")

    def extract_pose_map(self, input_image_path: str, output_pose_path: str) -> str:
        """
        Extracts human skeletal pose coordinates from a keyframe image.
        """
        self._init_detectors()
        if not self._pose_detector or not os.path.exists(input_image_path):
            return input_image_path  # Fallback to keyframe if detector is unavailable

        try:
            image = Image.open(input_image_path)
            pose_image = self._pose_detector(image)
            pose_image.save(output_pose_path)
            return output_pose_path
        except Exception as e:
            logger.error(f"Failed to generate pose map: {e}")
            return input_image_path