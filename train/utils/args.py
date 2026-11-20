import os
import argparse
import yaml

from utils.models import MODELS
from utils.augment import AUGMENTATIONS


# 'key=value' -> (key, value)
# value is parsed as YAML: 0.1 becomes a float, True a bool, anything else stays a string
def key_value(s):
    if '=' not in s:
        raise argparse.ArgumentTypeError(f'key=value 형식: {s}')
    k, v = s.split('=', 1)
    return k, yaml.safe_load(v)


# value from .env, or the default if it's missing or empty
def env(key, default, cast=str):
    v = os.getenv(key, '')
    return cast(v) if v.strip() else default


def get_args(argv=None, data_dir=None):
    p = argparse.ArgumentParser()

    # model / data
    p.add_argument('--model', default=env('TRAIN_MODEL', 'yolov8n'), choices=list(MODELS))
    p.add_argument('--data-dir', default=data_dir, help='우리 데이터 폴더 (기본: train/dataset)')

    # train (defaults can be overridden with TRAIN_* in .env)
    p.add_argument('--epochs', type=int, default=env('TRAIN_EPOCHS', 100, int))
    p.add_argument('--batch', type=int, default=env('TRAIN_BATCH', 16, int), help='-1 = GPU 메모리에 맞춰 자동')
    p.add_argument('--imgsz', type=int, default=env('IMG_SIZE', 640, int), help='ai/app.py 의 IMG_SIZE 와 같아야 함')
    p.add_argument('--optimizer', default='auto', help='auto, SGD, AdamW ...')
    p.add_argument('--lr', type=float, default=0.01, help='optimizer auto면 무시')
    p.add_argument('--patience', type=int, default=env('TRAIN_PATIENCE', 20, int), help='early stop')
    p.add_argument('--device', default=env('TRAIN_DEVICE', None), help='0, 0,1, cpu (기본: GPU 있으면 GPU)')
    p.add_argument('--workers', type=int, default=env('TRAIN_WORKERS', 4, int))
    p.add_argument('--seed', type=int, default=0)

    # aug
    p.add_argument('--aug', default=env('TRAIN_AUG', 'nir'), choices=AUGMENTATIONS, help='albumentations 프리셋')
    p.add_argument('--mosaic', type=float, default=1.0, help='0 = off')

    # any other ultralytics train argument
    # ex) --opt cos_lr=True dropout=0.1 fliplr=0
    p.add_argument('--opt', nargs='*', type=key_value, default=[], metavar='KEY=VALUE',
                   help='ultralytics train 인자 직접 전달')

    # output
    p.add_argument('--show', action='store_true', help='실시간 그래프 창')
    p.add_argument('--tensorboard', action='store_true')
    p.add_argument('--name', default=None, help='기본: {model}_{aug}_{시간}')

    # automatic run from run_all.py
    p.add_argument('--auto', action='store_true',
                   help='데이터가 없거나 지난 학습과 같으면 건너뜀 (run_all.py 가 사용)')
    p.add_argument('--force', action='store_true', help='같은 데이터여도 다시 학습')

    args = p.parse_args(argv)
    args.opt = dict(args.opt)
    return args
