import os
import json
import shutil
from datetime import datetime

# trained best.pt -> what the conveyor runtime (onem2m-conveyor/ai/app.py) loads
#
#   {out_dir}/detect.pt        TorchScript, output [1, 4+nc, 8400] (app.py does NMS)
#                              -> MODEL_BACKEND=torchscript (default), no ultralytics needed in docker
#   {out_dir}/best.pt          ultralytics checkpoint (MODEL_BACKEND=ultralytics + MODEL_PATH=models/best.pt)
#   {out_dir}/classes.txt      class names, line order = class id (= defectClass)
#   {out_dir}/model_info.json  what / when trained (fingerprint -> skip same training)


# best.pt -> best.torchscript next to it (CPU, fixed imgsz -> app.py IMG_SIZE must be the same)
def export_torchscript(best_pt, imgsz):
    from ultralytics import YOLO
    return YOLO(best_pt).export(format='torchscript', imgsz=imgsz, device='cpu')


# write to tmp then rename -> AI never sees a half-written file
def safe_copy(src, dst):
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + '.tmp'
    shutil.copy(src, tmp)
    os.replace(tmp, dst)


def read_info(out_dir):
    path = os.path.join(out_dir, 'model_info.json')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def deploy(best_pt, ts_path, class_names, out_dir, info=None,
           model_name='detect.pt', classes_name='classes.txt'):
    # TorchScript export failed -> best.pt as detect.pt (app.py falls back to ultralytics)
    safe_copy(ts_path or best_pt, os.path.join(out_dir, model_name))
    safe_copy(best_pt, os.path.join(out_dir, 'best.pt'))
    with open(os.path.join(out_dir, classes_name), 'w', encoding='utf-8') as f:
        f.write('\n'.join(class_names) + '\n')
    info = {'time': f'{datetime.now():%Y-%m-%d %H:%M}', 'classes': list(class_names),
            'torchscript': bool(ts_path), **(info or {})}
    with open(os.path.join(out_dir, 'model_info.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print('model deployed:', os.path.abspath(out_dir), '(' + ', '.join(
        [model_name, 'best.pt', classes_name, 'model_info.json']) + ')')
