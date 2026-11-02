import unittest

from services.render_settings import OutputFormat, TARGET_DIMENSIONS
from services.face_tracker import (
    FaceBox,
    FocusPoint,
    _nms,
    _suggest_zoom,
    face_center_to_crop_focus,
    face_center_to_crop_position,
    slice_focus_points,
    simplify_focus_points,
    smooth_focus_points,
)
from services.video_cutter import (
    CropKeyframe,
    _video_filter,
    build_crop_expression,
    build_vertical_crop_expression,
)


class FaceTrackerTests(unittest.TestCase):
    def test_face_center_maps_to_available_vertical_crop_travel(self):
        self.assertAlmostEqual(
            face_center_to_crop_focus(0.5, 1920, 1080), 0.5, places=3
        )
        self.assertLess(face_center_to_crop_focus(0.2, 1920, 1080), 0.2)
        self.assertGreater(face_center_to_crop_focus(0.8, 1920, 1080), 0.8)

    def test_smoothing_limits_abrupt_camera_movement(self):
        points = [
            FocusPoint(0, 0.1, 1),
            FocusPoint(0.5, 0.9, 1),
            FocusPoint(1.0, 0.9, 1),
        ]
        smoothed = smooth_focus_points(points, max_speed_per_second=0.2)
        self.assertLessEqual(abs(smoothed[1].focus - smoothed[0].focus), 0.101)
        self.assertLess(smoothed[1].focus, 0.9)

    def test_scene_cut_locks_immediately_without_visible_camera_travel(self):
        points = [
            FocusPoint(0, 0.1, 1, 0.4),
            FocusPoint(0.4, 0.9, 1, 0.7, scene_cut=True),
        ]
        smoothed = smooth_focus_points(points)
        self.assertAlmostEqual(smoothed[1].focus, 0.9)
        self.assertAlmostEqual(smoothed[1].vertical_focus, 0.7)

    def test_short_detection_gap_holds_the_existing_composition(self):
        points = [
            FocusPoint(0, 0.2, 1, 0.3),
            FocusPoint(0.4, 0.5, 0, 0.5),
            FocusPoint(0.8, 0.5, 0, 0.5),
        ]
        smoothed = smooth_focus_points(points, detection_hold_seconds=1.0)
        self.assertAlmostEqual(smoothed[1].focus, smoothed[0].focus)
        self.assertAlmostEqual(smoothed[2].focus, smoothed[0].focus)

    def test_crop_expression_interpolates_and_escapes_filter_commas(self):
        points = [FocusPoint(0, 0.25, 1), FocusPoint(2, 0.75, 1)]
        expression = build_crop_expression(points)
        self.assertIn("if(lt(t\\,2.000)\\,", expression)
        self.assertIn("(iw-ow)", expression)

    def test_smart_reframe_builds_horizontal_and_vertical_paths(self):
        points = [
            FocusPoint(0, 0.2, 1, 0.25),
            FocusPoint(2, 0.8, 1, 0.7),
        ]
        horizontal = build_crop_expression(points)
        vertical = build_vertical_crop_expression(points)
        self.assertIn("(iw-ow)", horizontal)
        self.assertIn("(ih-oh)", vertical)
        self.assertIn("0.700000", vertical)

    def test_vertical_position_uses_headroom_when_zoom_allows_travel(self):
        _x, y = face_center_to_crop_position(
            0.5,
            0.2,
            1920,
            1080,
            zoom=1.3,
        )
        self.assertLess(y, 0.5)

    def test_smart_zoom_is_conservative_for_multiple_people(self):
        single = _suggest_zoom(
            [0.12, 0.13, 0.12],
            detection_ratio=0.9,
            average_face_count=1.0,
        )
        group = _suggest_zoom(
            [0.12, 0.13, 0.12],
            detection_ratio=0.9,
            average_face_count=2.2,
        )
        self.assertLessEqual(group, 1.2)
        self.assertGreaterEqual(single, group)

    def test_overlapping_detector_results_are_deduplicated(self):
        boxes = [
            FaceBox(10, 10, 100, 100, 0.9),
            FaceBox(14, 13, 98, 98, 0.7),
            FaceBox(240, 20, 80, 80, 0.8),
        ]
        result = _nms(boxes)
        self.assertEqual(len(result), 2)
        self.assertIn(boxes[0], result)

    def test_simplifier_keeps_first_and_last_keyframes(self):
        points = [FocusPoint(index, 0.5 + index * 0.001, 1) for index in range(100)]
        result = simplify_focus_points(points, max_points=10)
        self.assertEqual(result[0], points[0])
        self.assertEqual(result[-1], points[-1])
        self.assertLessEqual(len(result), 10)

    def test_simplifier_preserves_scene_cut_keyframes(self):
        points = [
            FocusPoint(0, 0.5, 1),
            FocusPoint(1, 0.501, 1, scene_cut=True),
            FocusPoint(2, 0.502, 1),
        ]
        result = simplify_focus_points(points)
        self.assertEqual(len(result), 3)
        self.assertTrue(result[1].scene_cut)

    def test_cached_smart_path_can_be_trimmed_and_rebased_for_export(self):
        points = [
            FocusPoint(0, 0.1, 1, 0.2),
            FocusPoint(5, 0.6, 1, 0.5, scene_cut=True),
            FocusPoint(10, 0.9, 1, 0.8),
        ]
        result = slice_focus_points(points, 2, 8)
        self.assertEqual(result[0].time, 0)
        self.assertEqual(result[-1].time, 6)
        self.assertAlmostEqual(result[0].focus, 0.3)
        self.assertAlmostEqual(result[-1].focus, 0.78)
        self.assertTrue(result[1].scene_cut)

    def test_manual_reframe_interpolates_both_axes_and_applies_zoom(self):
        video_filter = _video_filter(
            "vertical",
            0.5,
            0.5,
            1.5,
            crop_keyframes=[
                CropKeyframe(0, 0.2, 0.3),
                CropKeyframe(4, 0.8, 0.7),
            ],
        )
        # Derived, not hardcoded: OUTPUT_HEIGHT is a config knob, and a test
        # that pins 720p breaks the moment quality is raised.
        width, height = TARGET_DIMENSIONS[OutputFormat.VERTICAL]
        zoomed = f"scale={round(width * 1.5 / 2) * 2}:{round(height * 1.5 / 2) * 2}"
        self.assertIn(zoomed, video_filter)
        self.assertIn(f"crop={width}:{height}", video_filter)
        self.assertIn("(iw-ow)", video_filter)
        self.assertIn("(ih-oh)", video_filter)
        self.assertIn("if(lt(t\\,4.000)", video_filter)


if __name__ == "__main__":
    unittest.main()
