import os
import json
import random
import shutil
import hashlib
import yaml

# our own dataset (YOLO txt labels) -> ultralytics folder
# (only standard library + yaml -> can be checked before torch / ultralytics are installed)
#
# accepted layouts (dataset root):
#   A) images/ + labels/                                  -> split automatically (SPLIT)
#      (labels can also sit next to the images: a.jpg + a.txt)
#   B) train/images, train/labels, valid(or val)/..., test/...   (Roboflow 'YOLOv8' export)
#   C) img/{train,valid,test} + labels/{train,valid,test}         (this repo's dataset/ folder)
#   D) CVAT 'YOLO 1.1' export: obj.names, obj.data, train.txt, obj_train_data/*.txt
#      + images anywhere (e.g. defect/*.png) -> matched by file name -> split like A
#   no valid -> 15% of train, no test -> test = valid
#
# class names: classes.txt / obj.names (one per line, line 0 = class 0) or data.yaml 'names'
# image without label file (or empty file) = normal product (background) -> fine, keep them
IMG_EXTS = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp')
SPLIT = (0.7, 0.15, 0.15)   # train / valid / test (layout A)
SEED = 42
SPLITS = ('train', 'valid', 'test')
SPLIT_ALIAS = {'train': 'train', 'valid': 'valid', 'val': 'valid', 'test': 'test'}
NAME_FILES = ('classes.txt', 'obj.names')                      # class names, one per line
NOT_LABELS = NAME_FILES + ('train.txt', 'valid.txt', 'val.txt', 'test.txt')   # CVAT image lists


def is_image(path):
    return path.lower().endswith(IMG_EXTS)


# every image under folder (sub folders too), sorted
def list_images(folder):
    out = []
    for root, _, files in os.walk(folder):
        out += [os.path.join(root, f) for f in files if is_image(f)]
    return sorted(out)


# number of images in the dataset folder (0 = nothing to train)
def count_images(src):
    if not os.path.isdir(src):
        return 0
    return len(list_images(src))


# every label txt under folder: file name (no ext) -> path
# (CVAT keeps labels in obj_train_data/ and images somewhere else)
def label_index(folder):
    out = {}
    for root, _, files in os.walk(folder):
        for f in files:
            if f.endswith('.txt') and f not in NOT_LABELS:
                out.setdefault(os.path.splitext(f)[0], os.path.join(root, f))
    return out


# image path -> label path or None
#   .../images/x.jpg -> .../labels/x.txt  (also img/ -> labels/)
#   .../x.jpg        -> .../x.txt         (labelImg default)
#   index[x]                              (same file name anywhere, CVAT)
def find_label(img, index=None):
    stem = os.path.splitext(img)[0]
    parts = stem.replace('\\', '/').split('/')
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in ('images', 'img'):
            p = '/'.join(parts[:i] + ['labels'] + parts[i + 1:]) + '.txt'
            if os.path.exists(p):
                return p
    if os.path.exists(stem + '.txt') and os.path.basename(stem) + '.txt' not in NOT_LABELS:
        return stem + '.txt'
    return (index or {}).get(os.path.basename(stem))


# classes.txt / obj.names or data.yaml -> ['stone', 'metal', ...]
def read_class_names(src):
    for root, _, files in os.walk(src):
        for name in NAME_FILES:
            if name in files:
                with open(os.path.join(root, name), encoding='utf-8') as f:
                    return [l.strip() for l in f if l.strip()]
    for root, _, files in os.walk(src):
        for name in ('data.yaml', 'dataset.yaml'):
            if name in files:
                with open(os.path.join(root, name), encoding='utf-8') as f:
                    names = (yaml.safe_load(f) or {}).get('names')
                if isinstance(names, dict):
                    return [names[k] for k in sorted(names)]
                if names:
                    return list(names)
    return []


# dataset root -> {'train': [...], 'valid': [...], 'test': [...]} or {None: [...]} (layout A, not split yet)
def collect(src):
    split = {}
    for d in os.listdir(src):                       # B) train/ valid/ test/
        if d.lower() in SPLIT_ALIAS and os.path.isdir(os.path.join(src, d)):
            split.setdefault(SPLIT_ALIAS[d.lower()], []).extend(list_images(os.path.join(src, d)))
    for top in ('img', 'images'):                   # C) img/train ...
        folder = os.path.join(src, top)
        if not os.path.isdir(folder):
            continue
        for d in os.listdir(folder):
            if d.lower() in SPLIT_ALIAS and os.path.isdir(os.path.join(folder, d)):
                split.setdefault(SPLIT_ALIAS[d.lower()], []).extend(list_images(os.path.join(folder, d)))
    if split.get('train'):
        return split
    return {None: list_images(src)}                 # A) everything


# list -> train / valid / test by ratio (at least 1 valid / 1 test when there are >= 3 images)
def split_list(items, split=SPLIT, seed=SEED):
    items = sorted(items)
    random.Random(seed).shuffle(items)
    n = len(items)
    n_val = max(1, round(n * split[1])) if n >= 3 else 0
    n_test = max(1, round(n * split[2])) if n >= 3 else 0
    return {'valid': items[:n_val], 'test': items[n_val:n_val + n_test], 'train': items[n_val + n_test:]}


# same, but each group (key) split separately -> every class in every split (small datasets)
def split_by(items, key, split=SPLIT, seed=SEED):
    groups = {}
    for x in items:
        groups.setdefault(key(x), []).append(x)
    out = {s: [] for s in SPLITS}
    for k in sorted(groups, key=str):
        for s, xs in split_list(groups[k], split, seed).items():
            out[s] += xs
    return out


# image -> its rarest class id (-1 = no box). used as split group
def rarest_class(img, index, totals):
    label = find_label(img, index)
    if not label:
        return -1
    ids = set()
    with open(label, encoding='utf-8') as f:
        for line in f:
            v = line.split()
            if v and v[0].lstrip('-').replace('.', '', 1).isdigit():
                ids.add(int(float(v[0])))
    return min(ids, key=lambda c: (totals.get(c, 0), c)) if ids else -1


# same files + same settings -> same fingerprint (used to skip re-training)
# file CONTENT, not modified time -> same value after zip / Colab / Google Drive copy
def fingerprint(src, extra=None):
    h = hashlib.md5()
    files = list_images(src) + [os.path.join(r, f) for r, _, fs in os.walk(src) for f in fs
                                if f.endswith(('.txt', '.yaml', '.names'))]
    for p in sorted(files, key=lambda p: os.path.relpath(p, src).replace('\\', '/')):
        h.update(os.path.relpath(p, src).replace('\\', '/').encode('utf-8') + b'|')
        with open(p, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    h.update(json.dumps(extra or {}, sort_keys=True).encode('utf-8'))
    return h.hexdigest()


# check one label file -> (number of boxes per class, bad lines)
def check_label(path, nc):
    counts, bad = [0] * nc, 0
    with open(path, encoding='utf-8') as f:
        for line in f:
            v = line.split()
            if not v:
                continue
            try:
                c = int(float(v[0]))
                ok = len(v) == 5 and 0 <= c < nc and all(0 <= float(x) <= 1 for x in v[1:])
            except ValueError:
                ok = False
            if ok:
                counts[c] += 1
            else:
                bad += 1
    return counts, bad


# our dataset -> {train,valid,test}/{images,labels} in out_dir
# - rebuilt only when the files changed (done.json)
# - returns (out_dir, class names)
def prepare_custom(src, out_dir, class_names=None, split=SPLIT, seed=SEED):
    src, out_dir = os.path.abspath(src), os.path.abspath(out_dir)
    names = list(class_names or read_class_names(src))
    if not names:
        raise SystemExit(f'클래스 이름이 없어요: {src} 에 classes.txt (한 줄에 하나) 또는 data.yaml(names) 를 넣어 주세요.')
    if not count_images(src):
        raise SystemExit(f'이미지가 없어요: {src}')

    params = {'src': src, 'names': names, 'split': list(split), 'seed': seed, 'fp': fingerprint(src)}
    marker = os.path.join(out_dir, 'done.json')
    if os.path.exists(marker):
        with open(marker, encoding='utf-8') as f:
            if json.load(f) == params:
                print('dataset path:', out_dir, '(cached)')
                return out_dir, names
    shutil.rmtree(out_dir, ignore_errors=True)

    index = label_index(src)
    found = collect(src)
    if None in found:   # split by rarest class -> stone / metal / plastic all in valid and test
        totals = {}
        for img in found[None]:
            label = find_label(img, index)
            if label:
                for c, n in enumerate(check_label(label, len(names))[0]):
                    totals[c] = totals.get(c, 0) + n
        imgs = found[None]
        found = split_by(imgs, lambda x: rarest_class(x, index, totals), split, seed)
        if not found['valid'] or not found['test']:   # groups too small -> plain random split
            found = split_list(imgs, split, seed)
    else:
        if not found.get('valid'):
            rest = split_list(found['train'], (0.85, 0.15, 0), seed)
            found['train'], found['valid'] = rest['train'] + rest['test'], rest['valid']
        if not found.get('test'):
            found['test'] = found['valid']   # no test set -> test score = valid score

    counts, bad, n_bg = [0] * len(names), 0, 0
    split_counts = {s: [0] * len(names) for s in SPLITS}
    for s in SPLITS:
        os.makedirs(os.path.join(out_dir, s, 'images'), exist_ok=True)
        os.makedirs(os.path.join(out_dir, s, 'labels'), exist_ok=True)
        for img in found.get(s, []):
            # sub/a.jpg -> sub__a.jpg (same name in two folders must not collide)
            key = os.path.relpath(img, src).replace('\\', '/').replace('/', '__')
            key = os.path.splitext(key)[0]
            shutil.copy2(img, os.path.join(out_dir, s, 'images', key + os.path.splitext(img)[1].lower()))
            label = find_label(img, index)
            if label:
                c, b = check_label(label, len(names))
                counts, bad = [x + y for x, y in zip(counts, c)], bad + b
                split_counts[s] = [x + y for x, y in zip(split_counts[s], c)]
                n_bg += sum(c) == 0
                shutil.copy2(label, os.path.join(out_dir, s, 'labels', key + '.txt'))
            else:
                n_bg += 1

    print(f'dataset: train {len(found["train"])} / valid {len(found["valid"])} / test {len(found["test"])} 장'
          f'  (정상=라벨 없음 {n_bg}장)')
    print('  boxes:', ', '.join(f'{n}={c}' for n, c in zip(names, counts)))
    for s in SPLITS:
        print(f'    {s:5s}:', ', '.join(f'{n}={c}' for n, c in zip(names, split_counts[s])))
    if bad:
        print(f'  !! 잘못된 라벨 줄 {bad}개는 학습에서 빠져요 (클래스 번호가 classes.txt 줄 수보다 크거나 좌표가 0~1 밖)')
    if not sum(counts):
        raise SystemExit('박스가 하나도 없어요. 라벨 txt 이름이 이미지 이름과 같은지, '
                         'YOLO 형식(class cx cy w h, 0~1)인지 확인하세요.')

    with open(marker, 'w', encoding='utf-8') as f:
        json.dump(params, f)
    print('dataset path:', out_dir)
    return out_dir, names


# ultralytics needs a data.yaml that points to the dataset folder
def write_data_yaml(data_dir, yaml_path, class_names=('stone',)):
    data = {
        'path': os.path.abspath(data_dir),
        'train': 'train/images',
        'val': 'valid/images',
        'test': 'test/images',
        'names': dict(enumerate(class_names)),
    }
    os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    return yaml_path
