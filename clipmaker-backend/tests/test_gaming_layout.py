import tempfile
import unittest
from pathlib import Path

from routers.jobs import RenderClipRequest
from pydantic import ValidationError
from services.caption_burner import build_ass_file
from services.gaming_layout import FacecamRegion, _Detection, choose_facecam_region
from services.render_settings import (
    CaptionPosition,
    FacecamCorner,
    LayoutMode,
    OutputFormat,
    TARGET_DIMENSIONS,
)
from services.video_cutter import build_gaming_filter


class GamingLayoutTests(unittest.TestCase):
    def test_repeated_stable_corner_face_wins(self):
        detections = [
            _Detection(index, .82 + (index % 2) * .004, .74, .07, .12, .92, FacecamCorner.BOTTOM_RIGHT)
            for index in range(12)
        ]
        # A one-frame cutscene face should not replace the stable OBS camera.
        detections.append(_Detection(4, .06, .06, .12, .18, .99, FacecamCorner.TOP_LEFT))
        result = choose_facecam_region(detections, 12)
        self.assertEqual(result.corner, FacecamCorner.BOTTOM_RIGHT)
        self.assertFalse(result.used_fallback)
        self.assertGreaterEqual(result.detection_ratio, .95)
        self.assertGreater(result.region.x, .5)
        self.assertGreater(result.region.y, .45)

    def test_missing_detection_uses_requested_corner(self):
        result = choose_facecam_region([], 12, FacecamCorner.TOP_LEFT)
        self.assertTrue(result.used_fallback)
        self.assertEqual(result.corner, FacecamCorner.TOP_LEFT)
        self.assertLess(result.region.x, .05)
        self.assertLess(result.region.y, .05)

    def test_filter_builds_65_35_vertical_composition(self):
        graph = build_gaming_filter(FacecamRegion(.67, .64, .31, .34))
        self.assertIn("split=2", graph)
        # The 65/35 split is the contract here, not any particular resolution.
        width, height = TARGET_DIMENSIONS[OutputFormat.VERTICAL]
        gameplay_height = round(height * 0.65 / 2) * 2
        self.assertIn(f"scale={width}:{gameplay_height}", graph)
        self.assertIn(f"scale={width}:{height - gameplay_height}", graph)
        self.assertIn("vstack=inputs=2", graph)
        self.assertTrue(graph.endswith("[vout]"))

    def test_render_payload_accepts_manual_facecam_region(self):
        payload = RenderClipRequest(
            start=0,
            end=10,
            layout_mode=LayoutMode.GAMEPLAY_REACTION,
            facecam_corner=FacecamCorner.BOTTOM_RIGHT,
            facecam_region={"x": .68, "y": .64, "width": .3, "height": .34},
        )
        self.assertEqual(payload.layout_mode, LayoutMode.GAMEPLAY_REACTION)

    def test_render_payload_rejects_facecam_outside_source(self):
        with self.assertRaises(ValidationError):
            RenderClipRequest(
                start=0,
                end=10,
                layout_mode=LayoutMode.GAMEPLAY_REACTION,
                facecam_region={"x": .8, "y": .7, "width": .3, "height": .4},
            )

    def test_gaming_caption_sits_at_composition_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gaming.ass"
            build_ass_file(
                [{"word": "Victory", "start": 0, "end": .5}],
                path,
                0,
                position=CaptionPosition.GAMING,
            )
            style = next(
                line for line in path.read_text().splitlines()
                if line.startswith("Style: Caption")
            )
        self.assertIn(",2,72,72,435,", style)


if __name__ == "__main__":
    unittest.main()
