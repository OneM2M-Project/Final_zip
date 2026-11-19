# X-Guard 모델 학습 (YOLOv8n) -> 컨베이어 AI 가 읽는 models/detect.pt 로 바로 저장
#
#   1) train/dataset/ 에 우리 데이터를 넣어요 (형식은 train/dataset/README.md)
#   2) run_all.bat / python run_all.py 를 실행하면 시작할 때 자동으로 학습해요 (python train/train.py --auto)
#      PC 에 GPU 가 없으면 자동 학습은 건너뛰어요 -> Colab GPU 로: train/colab_train.ipynb, python train/colab.py -h
#      손으로:  python train/train.py            # 같은 데이터여도 학습
#               python train/train.py --epochs 50 --batch 8
#               python train/train.py -h
#
# --auto: 데이터 없음 -> 건너뜀 / 데이터·설정이 지난 학습과 같고 models/detect.pt 있음 -> 건너뜀
#         GPU 없음 (TRAIN_DEVICE=cpu 가 아니면) -> 건너뜀 (CPU 학습은 몇 시간 걸려요)
#
# 결과
#   models/detect.pt, best.pt, classes.txt, model_info.json   ai/app.py 가 읽는 곳 (.env MODEL_PATH, CLASSES_PATH)
#   train/models/runs/{name}/        로그, 그래프, live.png
#   train/models/weights/{name}.pt   실험별 best 모델
#   train/models/results.csv         실험별 test mAP 누적
import os
import sys
import csv
import shutil
from datetime import datetime

TRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TRAIN_DIR)
sys.path.append(ROOT)
from common import m2m  # noqa: E402,F401  (.env / env.example 를 읽어요)

from utils.args import get_args  # noqa: E402
from utils.custom import prepare_custom, count_images, fingerprint, write_data_yaml  # noqa: E402
from utils.deploy import read_info  # noqa: E402

DATA_DIR = os.path.join(TRAIN_DIR, 'dataset')                     # 여기에 데이터를 넣어요
CACHE_DIR = os.path.join(TRAIN_DIR, 'models', 'dataset_yolo')     # ultralytics 형식으로 바꾼 사본
RUNS_DIR = os.path.join(TRAIN_DIR, 'models', 'runs')
WEIGHTS_DIR = os.path.join(TRAIN_DIR, 'models', 'weights')
RESULTS_CSV = os.path.join(TRAIN_DIR, 'models', 'results.csv')
MODEL_PATH = os.path.join(ROOT, os.getenv('MODEL_PATH', 'models/detect.pt'))
CLASSES_PATH = os.path.join(ROOT, os.getenv('CLASSES_PATH', 'models/classes.txt'))
MODEL_DIR = os.path.dirname(MODEL_PATH)


# 실험 1개 -> results.csv 한 줄
def log_result(args, name, metrics):
    row = {
        'time': f'{datetime.now():%Y-%m-%d %H:%M}',
        'name': name,
        'model': args.model,
        'aug': args.aug,
        'epochs': args.epochs,
        'batch': args.batch,
        'imgsz': args.imgsz,
        'optimizer': args.optimizer,
        'lr': args.lr,
        'mosaic': args.mosaic,
        'opt': ' '.join(f'{k}={v}' for k, v in args.opt.items()),
        'mAP50': round(metrics.box.map50, 4),
        'mAP50_95': round(metrics.box.map, 4),
    }
    new = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


# 학습 결과를 바꾸는 설정 (같으면 다시 학습할 필요 없음)
def train_key(args):
    return {'model': args.model, 'epochs': args.epochs, 'imgsz': args.imgsz, 'aug': args.aug,
            'mosaic': args.mosaic, 'optimizer': args.optimizer, 'lr': args.lr, 'opt': args.opt}


def main():
    args = get_args(data_dir=DATA_DIR)
    src = os.path.abspath(args.data_dir)

    # 0) 학습할지 결정 (torch / ultralytics 없이도 여기까지는 돌아요)
    n = count_images(src)
    if not n:
        print(f'학습 데이터 없음 ({src}) -> 학습 건너뜀')
        return 0
    fp = fingerprint(src, train_key(args))
    if args.auto and not args.force and os.path.exists(MODEL_PATH) and read_info(MODEL_DIR).get('fingerprint') == fp:
        print(f'데이터·설정이 지난 학습과 같아요 -> 학습 건너뜀 ({os.path.relpath(MODEL_PATH, ROOT)} 그대로 사용)')
        return 0
    try:
        import torch
        has_gpu = torch.cuda.is_available()
    except ImportError:
        has_gpu = False
    if args.auto and not has_gpu and str(args.device).lower() != 'cpu':
        state = '데이터가 바뀌었는데 ' if os.path.exists(MODEL_PATH) else ''
        print(f'{state}이 PC 에는 GPU 가 없어서 자동 학습을 건너뛰어요.')
        print('   Colab GPU 로 학습: python train/colab.py pack -> train/colab_train.ipynb -> python train/colab.py install <받은 zip>')
        print('   그래도 CPU 로 학습하려면 .env 에 TRAIN_DEVICE=cpu')
        return 0

    try:
        from ultralytics import settings
        from utils.models import load_model
        from utils.augment import get_augmentations
        from utils.plot import LivePlot
        from utils.deploy import export_torchscript, deploy
        get_augmentations(args.aug)   # albumentations 설치 확인
    except ImportError as e:
        print('!! 학습 패키지가 없어요:', e)
        print('   pip install -r train/requirements.txt  (GPU면 README 의 torch 설치 줄 먼저)')
        return 1

    print(f'학습 시작: {args.model}, 이미지 {n}장, epochs {args.epochs}, imgsz {args.imgsz}')
    name = args.name or f'{args.model}_{args.aug}_{datetime.now():%Y%m%d_%H%M%S}'

    # 1) data
    data_dir, class_names = prepare_custom(src, CACHE_DIR)
    data_yaml = write_data_yaml(data_dir, os.path.join(RUNS_DIR, name, 'data.yaml'), class_names)
    print('classes:', class_names)

    # 2) model
    settings.update({'tensorboard': args.tensorboard})
    model = load_model(args.model)
    model.add_callback('on_fit_epoch_end', LivePlot(show=args.show))

    # 3) train (끝나면 best.pt 로드된 상태)
    train_args = dict(
        data=data_yaml,
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        optimizer=args.optimizer,
        lr0=args.lr,
        patience=args.patience,
        device=args.device,
        workers=args.workers,
        seed=args.seed,
        mosaic=args.mosaic,
        project=RUNS_DIR,
        name=name,
        exist_ok=True,
    )
    aug = get_augmentations(args.aug)
    if aug is not None:   # None = ultralytics 기본
        train_args['augmentations'] = aug
    train_args.update(args.opt)   # --opt 우선
    model.train(**train_args)

    # 4) save
    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    save_path = os.path.join(WEIGHTS_DIR, name + '.pt')
    shutil.copy(model.trainer.best, save_path)
    print('model saved:', save_path)

    # 5) test
    metrics = model.val(data=data_yaml, split='test', imgsz=args.imgsz, batch=args.batch,
                        device=args.device, project=RUNS_DIR, name=name + '_test', exist_ok=True)
    print(f'test  mAP@50: {metrics.box.map50:.4f}  mAP@50:95: {metrics.box.map:.4f}')
    log_result(args, name, metrics)

    # 6) ai/app.py 가 읽는 곳으로 (TorchScript 실패해도 best.pt 로 동작)
    ts = None
    try:
        ts = export_torchscript(save_path, args.imgsz)
    except Exception as e:
        print('!! TorchScript 변환 실패 -> best.pt 를 detect.pt 로 둬요 (app.py 가 ultralytics 로 읽음):', repr(e))
    deploy(save_path, ts, class_names, MODEL_DIR, model_name=os.path.basename(MODEL_PATH),
           classes_name=os.path.relpath(CLASSES_PATH, MODEL_DIR),
           info={'name': name, 'model': args.model, 'imgsz': args.imgsz, 'epochs': args.epochs,
                 'images': n, 'mAP50': round(metrics.box.map50, 4), 'mAP50_95': round(metrics.box.map, 4),
                 'fingerprint': fp})
    return 0


if __name__ == '__main__':   # Windows 필수 (worker가 이 파일 다시 import)
    sys.exit(main())
