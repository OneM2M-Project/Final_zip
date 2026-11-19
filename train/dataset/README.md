# train/dataset — 학습 데이터 넣는 곳

여기에 라벨링한 근적외선(NIR) 사진을 넣고 `run_all.bat` 을 실행하면 시작할 때 자동으로 YOLOv8n 을 학습해서
`models/detect.pt` 로 저장해요. 데이터가 바뀌지 않았으면 다시 학습하지 않아요 (`python train/train.py` 는 항상 학습).

## 넣는 방법 (셋 중 아무거나)

```
A) train/dataset/images/*.jpg   train/dataset/labels/*.txt   train/dataset/classes.txt   → 7:1.5:1.5 자동 분할
B) train/dataset/train|valid|test/images|labels   train/dataset/data.yaml               (Roboflow "YOLOv8" 내보내기 압축 풀기)
C) train/dataset/img/train|test   train/dataset/labels/train|test   classes.txt
D) CVAT "YOLO 1.1" 내보내기 압축을 그대로 풀고 + 이미지 폴더       → 파일 이름으로 짝 맞춤, 7:1.5:1.5 자동 분할
   train/dataset/obj.names  obj.data  train.txt  obj_train_data/*.txt
   train/dataset/defect/*.png                                     (지금 넣어 둔 데이터가 이 형식)
```

자동 분할은 클래스별로 나눠서 valid/test 에도 모든 클래스가 들어가요.

- 라벨(txt) 한 줄 = 박스 하나: `클래스번호 cx cy w h` (0~1, 이미지 크기로 나눈 값). labelImg / Roboflow / CVAT 의 YOLO 형식 그대로.
- `classes.txt`(CVAT 는 `obj.names`): 한 줄에 하나, 첫 줄이 0번. 예) `stone` `metal` `plastic`. 이 이름이 대시보드의 `defectClass` 가 돼요.
- 정상 제품 사진은 라벨 없이(또는 빈 txt) 같이 넣어 주세요. 정상을 불량으로 잘못 보는 걸 줄여 줘요.
- 실제 카메라(ELP OV9281, 850nm)로 찍은 사진을 쓰세요. `.env` 에서 `SAVE_CAPTURES=1` 이면 시연 중 찍힌 사진이 `captures/` 에 모여요.
