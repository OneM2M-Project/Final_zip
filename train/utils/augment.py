# ultralytics ALWAYS applies its own augmentation too: mosaic, scale/translate, left-right flip, HSV
# (mosaic can be turned off with --mosaic 0)
# the lists below are EXTRA albumentations transforms
# spatial transforms (flip, rotate, ...) -> boxes are updated automatically

# nir: 850nm transmission photo from the side camera (product always upright)
#      -> exposure / noise / blur only, no vertical flip / 90 rotation
AUGMENTATIONS = ('default', 'none', 'xray', 'nir', 'strong')


def get_augmentations(name):
    import albumentations as A   # here -> `train.py -h` works before it is installed

    # ultralytics built-in albumentations (Blur, MedianBlur, ToGray, CLAHE, 1% each)
    if name == 'default':
        return None

    # no albumentations
    if name == 'none':
        return []

    # x-ray like changes
    xray = [
        A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.5),  # tube power / exposure
        A.RandomGamma(gamma_limit=(80, 120), p=0.3),                                  # detector response
        A.GaussNoise(std_range=(0.01, 0.05), p=0.3),                                  # quantum noise
        A.Blur(blur_limit=3, p=0.1),                                                  # focal spot blur
        A.VerticalFlip(p=0.5),                                                        # object can be upside down
        A.RandomRotate90(p=0.5),
    ]
    if name == 'xray':
        return xray

    # NIR camera (ELP OV9281) changes: LED brightness, exposure, sensor noise, small motion
    if name == 'nir':
        return [
            A.RandomBrightnessContrast(brightness_limit=0.25, contrast_limit=0.25, p=0.5),  # LED / exposure
            A.RandomGamma(gamma_limit=(80, 120), p=0.3),
            A.GaussNoise(std_range=(0.01, 0.04), p=0.3),                                    # sensor noise
            A.MotionBlur(blur_limit=5, p=0.15),                                             # belt moving
            A.CLAHE(p=0.1),
        ]

    # xray + harder image quality changes
    if name == 'strong':
        return xray + [
            A.CLAHE(p=0.3),
            A.MotionBlur(blur_limit=5, p=0.2),
            A.Downscale(scale_range=(0.5, 0.9), p=0.2),   # lower resolution detector
        ]

    raise ValueError(f'unknown augmentation: {name} (choose from {AUGMENTATIONS})')
