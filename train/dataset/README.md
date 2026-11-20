# train/dataset: training data

Put labeled near-infrared (NIR) photos here and run `run_all.bat`. On startup it trains YOLOv8n and
saves the result to `models/detect.pt`. If the data hasn't changed since the last run it skips
training (`python train/train.py` always trains).

## Layouts it understands

```
A) train/dataset/images/*.jpg   train/dataset/labels/*.txt   train/dataset/classes.txt   -> split 7:1.5:1.5 automatically
B) train/dataset/train|valid|test/images|labels   train/dataset/data.yaml               (unzipped Roboflow "YOLOv8" export)
C) train/dataset/img/train|test   train/dataset/labels/train|test   classes.txt
D) an unzipped CVAT "YOLO 1.1" export plus an image folder    -> paired by file name, split 7:1.5:1.5
   train/dataset/obj.names  obj.data  train.txt  obj_train_data/*.txt
   train/dataset/defect/*.png                                     (the data in here now uses this layout)
```

The automatic split is done per class, so valid and test also get every class.

- Each line of a label file is one box: `class_id cx cy w h`, all 0-1 (divided by the image size). This is the YOLO format used by labelImg, Roboflow and CVAT.
- `classes.txt` (`obj.names` for CVAT) has one name per line; the first line is class 0, e.g. `stone`, `metal`, `plastic`. These names show up as `defectClass` on the dashboard.
- Include photos of normal products too, with no label file or an empty one. They cut down on false alarms.
- Use photos from the real camera (ELP OV9281, 850 nm). With `SAVE_CAPTURES=1` in `.env`, frames taken during a run are collected in `captures/`.
