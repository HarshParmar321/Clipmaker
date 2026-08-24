# Local vision models

`face_detection_yunet_2023mar.onnx` is the official YuNet face detector from
the [OpenCV Model Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet).
It runs locally through OpenCV; no frames are sent to a third-party vision API.

OpenCV Zoo is distributed under the Apache License 2.0. The model directory in
the upstream repository identifies YuNet's original training source and paper.
Keep this notice with the model when packaging or distributing Clipmaker.

If the model is missing or cannot be loaded, Clipmaker automatically falls back
to OpenCV frontal/profile Haar detection and ultimately to a centered crop.
