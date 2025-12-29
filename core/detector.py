from ultralytics import YOLO
import torch


class Detector:
    def __init__(self, model_path="yolov8n.pt", device=0):
        self.device = device if torch.cuda.is_available() else "cpu"
        print(f"[Detector] Используется устройство: {self.device}")

        self.model = YOLO(model_path)
        print("[Detector] Модель загружена")

    def detect(self, frame):
        """
        frame — numpy.ndarray (BGR, OpenCV)
        Возвращает список боксов + классы + вероятности
        """
        results = self.model(frame, device=self.device, verbose=False)[0]

        detections = []
        for box in results.boxes:
            cls_id = int(box.cls)
            conf = float(box.conf)
            xyxy = box.xyxy[0].tolist()

            detections.append({
                "class_id": cls_id,
                "class_name": self.model.names[cls_id],
                "conf": conf,
                "bbox": xyxy
            })

        return detections
