# models/

Colab 노트북(`train/colab_train.ipynb`)이 Drive 의 `ROOT/model/` 에 만든 파일을 여기에 복사해요 (또는 GPU PC 에서 `train/train.py`).

- `detect.pt`       : YOLOv8n TorchScript (출력 `[1, 4+클래스수, 8400]`, README "detect.pt 규격" 형식 B). ai/app.py 기본값으로 읽혀요.
- `best.pt`         : 같은 모델의 ultralytics 체크포인트 (`MODEL_BACKEND=ultralytics` 로 쓸 때).
- `classes.txt`     : 클래스 번호 순서대로 한 줄에 하나 (0번 = 첫 줄). 이 이름이 `defectClass`가 돼요. 학습 데이터의 classes.txt 로 덮어써요.
- `model_info.json` : 언제·몇 장으로 학습했는지, 입력 크기(imgsz), mAP. ai/app.py 가 imgsz 를 여기서 읽어요.
