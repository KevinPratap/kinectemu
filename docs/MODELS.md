# Models

Model files live in `models/` (git-ignored). Each was downloaded from its publisher's official URL, the same URLs `rtmlib` 0.0.16 uses for RTMO.

| Model | File | Source | Licence | SHA-256 |
|---|---|---|---|---|
| RTMO-s body7 640 | `models/rtmo-s/end2end.onnx` | download.openmmlab.com (`rtmo-s_8xb32-600e_body7-640x640-dac2bf74_20231211.zip`) | Apache-2.0 (MMPose) | `D0703D40D19F3921DA51AE725402D5FDAE4D2478C7442072D3101BD396F370D8` |
| RTMO-m body7 640 | `models/rtmo-m/end2end.onnx` | download.openmmlab.com (`rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip`) | Apache-2.0 (MMPose) | `750D872151FF4652F02C738EFC3A547E112CE9B688FE920BC17F948E8C3AFDAC` |
| MediaPipe Pose Landmarker lite (fp16) | `models/mp-pose-lite/pose_landmarker_lite.task` | storage.googleapis.com/mediapipe-models | Apache-2.0 | `59929E1D1EE95287735DDD833B19CF4AC46D29BC7AFDDBBF6753C459690D574A` |
| MediaPipe Pose Landmarker full (fp16) | `models/mp-pose-full/pose_landmarker_full.task` | storage.googleapis.com/mediapipe-models | Apache-2.0 | `4EAA5EB7A98365221087693FCC286334CF0858E2EB6E15B506AA4A7ECDCEC4AD` |
| MediaPipe Pose Landmarker heavy (fp16) | `models/mp-pose-heavy/pose_landmarker_heavy.task` | storage.googleapis.com/mediapipe-models | Apache-2.0 | `64437AF838A65D18E5BA7A0D39B465540069BC8AAE8308DE3E318AAD31FCBC7B` |

## Converted models (derived from the files above)

The landmark model was extracted from the `.task` zip (`pose_landmarks_detector.tflite`) and converted with tf2onnx 1.16.1 (`--tflite ... --opset 17`, TF 2.15.1, in `tools/.venv-convert`). Equivalence to the original was checked by `tools/phase0/verify_landmark_onnx.py` on 5 real crops. Largest differences: 0.004 px on landmarks, 0.02 mm on world joints, 0.0000 on the presence flag, on both CPU and the RTX 4050 via DirectML.

| Model | File | From | SHA-256 |
|---|---|---|---|
| MediaPipe landmarks lite (ONNX) | `models/mp-pose-lite/pose_landmarks_lite.onnx` | `extracted/pose_landmarks_detector.tflite` (`AD6CFD3C…3E58`) | `D227DA716233676A5C190A5A133A82D53332106983A6E5C4F37602EE41C3F943` |
| MediaPipe landmarks full (ONNX) | `models/mp-pose-full/pose_landmarks_full.onnx` | `extracted/pose_landmarks_detector.tflite` (`82BE6D59…C488`) | `DA6D0A1BF955A2132E17BBA8F53A557232F37E1333079F78A0DD59FFDDD34E4A` |

I/O: input `input_1` [1,256,256,3] RGB in [0,1]. Outputs:
- `Identity`: 39×(x, y, z, visibility logit, presence logit), in 256-px crop units. Landmarks 33 and 34 are the hip-centre and above-head points used for the tracking ROI.
- `Identity_1`: pose-present logit.
- `Identity_2`: 256×256 segmentation logits.
- `Identity_3`: heatmaps.
- `Identity_4`: 39×3 world landmarks in metres, hip-centred.

Note: RTMO's training data (body7) mixes several public datasets, each with its own terms. Review them before any distribution.
