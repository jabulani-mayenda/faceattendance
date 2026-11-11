# Face model attribution

FaceTrack downloads the following OpenCV Zoo model files on request and checks their SHA-256 digests before use:

- YuNet detector, `face_detection_yunet_2023mar.onnx`, SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`, listed under MIT in OpenCV Zoo.
- SFace recognizer, `face_recognition_sface_2021dec.onnx`, SHA-256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`, listed under Apache 2.0 in OpenCV Zoo.

Source and license details: [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) and [OpenCV Zoo SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface). Review model and training-data terms for your intended deployment.
