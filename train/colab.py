# Colab GPU 로 학습할 때 PC 쪽에서 쓰는 도구
#
#   python train/colab.py pack                       # 학습 코드 + train/dataset -> xguard_train.zip
#   (Colab 에서 train/colab_train.ipynb 실행 -> xguard_model.zip 다운로드)
#   python train/colab.py install xguard_model.zip   # 받은 모델을 models/ 에 넣기
#
# install 뒤에는 데이터가 같으니 run_all 이 다시 학습하지 않고 그 모델로 켜져요.
import os
import sys
import json
import zipfile
import argparse

TRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TRAIN_DIR)
PACK = ['train/train.py', 'train/requirements.txt', 'train/utils', 'train/dataset',
        'common/__init__.py', 'common/m2m.py', 'env.example', '.env']
SKIP_DIRS = ('__pycache__',)
MODEL_FILES = ('detect.pt', 'best.pt', 'classes.txt', 'model_info.json')


# PACK 목록 -> zip (zip 안 경로 = 프로젝트 루트 기준)
def pack(out):
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for item in PACK:
            path = os.path.join(ROOT, item)
            if os.path.isfile(path):
                z.write(path, item)
                n += 1
            for root, dirs, files in os.walk(path) if os.path.isdir(path) else []:
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for f in files:
                    p = os.path.join(root, f)
                    z.write(p, os.path.relpath(p, ROOT).replace('\\', '/'))
                    n += 1
    print(f'{out} ({n}개 파일, {os.path.getsize(out) / 1e6:.1f}MB)')
    print('-> Colab 에서 train/colab_train.ipynb 를 열고 이 zip 을 올려 주세요 (또는 Google Drive 내 드라이브에 복사).')


# Colab 이 만든 zip 에서 models/ 파일만 꺼내기
def install(src):
    out_dir = os.path.join(ROOT, 'models')
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if os.path.basename(n) in MODEL_FILES]
        if 'detect.pt' not in [os.path.basename(n) for n in names]:
            raise SystemExit(f'{src} 안에 detect.pt 가 없어요. Colab 마지막 셀이 만든 xguard_model.zip 인지 확인하세요.')
        for n in names:
            tmp = os.path.join(out_dir, os.path.basename(n) + '.tmp')
            with z.open(n) as f, open(tmp, 'wb') as g:
                g.write(f.read())
            os.replace(tmp, os.path.join(out_dir, os.path.basename(n)))
            print('  models/' + os.path.basename(n))
    info_path = os.path.join(out_dir, 'model_info.json')
    if os.path.exists(info_path):
        with open(info_path, encoding='utf-8') as f:
            info = json.load(f)
        print(f"모델 설치 완료: {info.get('model')} {info.get('time')} 클래스={info.get('classes')} "
              f"test mAP50={info.get('mAP50')}")
    print('-> run_all.bat 을 실행하면 이 모델로 AI 가 켜져요.')


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('pack', help='학습 코드 + 데이터를 Colab 용 zip 으로')
    a.add_argument('--out', default=os.path.join(ROOT, 'xguard_train.zip'))
    b = sub.add_parser('install', help='Colab 에서 받은 모델 zip 을 models/ 에')
    b.add_argument('zip', help='xguard_model.zip')
    args = p.parse_args()
    if args.cmd == 'pack':
        pack(args.out)
    else:
        install(args.zip)


if __name__ == '__main__':
    sys.exit(main())
