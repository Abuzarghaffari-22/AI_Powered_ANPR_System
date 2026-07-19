import os, cv2, time, warnings
os.environ.update({'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '4',
                   'MKL_NUM_THREADS': '4', 'TORCH_CPP_LOG_LEVEL': 'ERROR',
                   'CYSIGNALS_CRASH_LOGS': ''})
warnings.filterwarnings('ignore')

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env', override=True)

from pipeline import (get_model, read_plate_ocr, _is_valid_plate_region,
                      CONF_THRESHOLD, YOLO_INPUT_W, BLUR_THRESHOLD)

DATA_DIR = Path('/home/abuzar-ghaffari/Final Year/ANPR_Project/FYP/data/car_data/car_data')
EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.JPG', '.JPEG', '.PNG', '.WEBP'}
images = sorted([p for p in DATA_DIR.iterdir() if p.suffix in EXTS])
print(f'Images found  : {len(images)}')
print(f'CONF_THRESHOLD: {CONF_THRESHOLD}')
print(f'YOLO_INPUT_W  : {YOLO_INPUT_W}')
print(f'BLUR_THRESHOLD: {BLUR_THRESHOLD}')

model = get_model()
print('YOLO warmed up — running benchmark...\n')

detected = 0
no_det   = 0
ocr_ok   = 0
ocr_fail = 0
read_err = 0

no_box_files  = []   # (name, best_conf)
ocr_fail_files = []  # (name, yolo_conf, crop_shape)
success_list   = []  # (name, plate, yolo_conf, ocr_conf)

t_start = time.perf_counter()

for img_path in images:
    img = cv2.imread(str(img_path))
    if img is None:
        read_err += 1
        no_box_files.append((img_path.name, 0.0))
        continue

    fh, fw = img.shape[:2]
    if fw > YOLO_INPUT_W:
        scale = YOLO_INPUT_W / fw
        yf    = cv2.resize(img, (YOLO_INPUT_W, int(fh * scale)),
                           interpolation=cv2.INTER_LINEAR)
    else:
        yf, scale = img.copy(), 1.0

    out = model(yf, verbose=False, imgsz=YOLO_INPUT_W)

    best_coords, best_conf = None, 0.0
    for r in out:
        if r.boxes is None:
            continue
        for box in r.boxes:
            c = float(box.conf[0])
            if c < CONF_THRESHOLD:
                continue
            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
            if scale != 1.0:
                bx1 = int(bx1 / scale); by1 = int(by1 / scale)
                bx2 = int(bx2 / scale); by2 = int(by2 / scale)
            if _is_valid_plate_region(bx1, by1, bx2, by2, fw, fh) and c > best_conf:
                best_conf, best_coords = c, (bx1, by1, bx2, by2)

    if best_coords is None:
        no_det += 1
        no_box_files.append((img_path.name, round(best_conf, 3)))
        continue

    detected += 1
    x1, y1, x2, y2 = best_coords
    px = max(20, int((x2 - x1) * 0.12))
    py = max(14, int((y2 - y1) * 0.18))
    crop = img[max(0, y1 - py):min(fh, y2 + py),
               max(0, x1 - px):min(fw, x2 + px)]

    plate, ocr_conf, _ = read_plate_ocr(crop)
    if plate:
        ocr_ok += 1
        success_list.append((img_path.name, plate,
                              round(best_conf, 3), round(ocr_conf, 3)))
    else:
        ocr_fail += 1
        ocr_fail_files.append((img_path.name, round(best_conf, 3),
                                crop.shape if crop is not None else None))

elapsed = time.perf_counter() - t_start
n = len(images)

print(f'=== BENCHMARK RESULTS ({n} images, {elapsed:.1f}s, {elapsed/n*1000:.0f}ms/img) ===')
print(f'Read errors       : {read_err}')
print(f'YOLO detected     : {detected}/{n} = {detected/n*100:.1f}%')
print(f'No YOLO detection : {no_det}/{n}   = {no_det/n*100:.1f}%')
print(f'OCR success       : {ocr_ok}/{n}   = {ocr_ok/n*100:.1f}%')
print(f'OCR fail (plate ok): {ocr_fail}/{n} = {ocr_fail/n*100:.1f}%')
print(f'\nEnd-to-end success: {ocr_ok}/{n} = {ocr_ok/n*100:.1f}%')

print(f'\n--- Sample successes (first 15) ---')
for name, plate, yc, oc in success_list[:15]:
    print(f'  {name}: "{plate}"  yolo={yc:.2f} ocr={oc:.2f}')

print(f'\n--- No-YOLO failures ({len(no_box_files)}, first 25) ---')
for name, c in no_box_files[:25]:
    print(f'  {name}  best_conf={c:.3f}')

print(f'\n--- OCR failures ({len(ocr_fail_files)}, first 25) ---')
for name, c, sh in ocr_fail_files[:25]:
    print(f'  {name}  yolo={c:.3f}  crop={sh}')
