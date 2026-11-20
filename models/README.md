# models/

Copy the files the Colab notebook (`train/colab_train.ipynb`) writes to `ROOT/model/` on Drive into this folder. `train/train.py` on a GPU PC writes them here directly.

- `detect.pt`: YOLOv8n as TorchScript, output `[1, 4+num_classes, 8400]` (format B in the README). `ai/app.py` loads this by default.
- `best.pt`: the same model as an ultralytics checkpoint, for `MODEL_BACKEND=ultralytics`.
- `classes.txt`: class names in index order, one per line (first line is class 0). These become `defectClass`. Overwrite it with the training data's classes.txt.
- `model_info.json`: when and on how many images the model was trained, the input size (imgsz) and mAP. `ai/app.py` reads imgsz from here.
