import os

# model name -> COCO pretrained weights (downloaded automatically on first use)
# all trained with the same ultralytics loop -> fair comparison
MODELS = {
    'yolov8n': 'yolov8n.pt',   # project model (small + fast on CPU / MEC pod)
    'yolov8':  'yolov8m.pt',   # baseline (anchor-free, C2f)
    'yolo11':  'yolo11m.pt',   # newer backbone (C3k2 + C2PSA attention)
    'yolov10': 'yolov10m.pt',  # NMS-free (one-to-one head)
    'yolo12':  'yolo12m.pt',   # attention-centric (area attention)
    'rtdetr':  'rtdetr-l.pt',  # transformer detector (DETR family, no NMS)
}


# pretrained weights are downloaded here (not into the current folder)
PRETRAINED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'pretrained')


# model name ('yolov8n', ...) or path to a saved .pt -> ultralytics model
# (ultralytics imported here -> `train.py -h` works before it is installed)
def load_model(name):
    from ultralytics import YOLO, RTDETR
    if name in MODELS:
        os.makedirs(PRETRAINED_DIR, exist_ok=True)
        weights = os.path.join(PRETRAINED_DIR, MODELS[name])
    else:
        weights = name   # e.g. models/weights/yolov8n_nir_20261105_160000.pt
    if 'rtdetr' in os.path.basename(weights):
        return RTDETR(weights)
    return YOLO(weights)
