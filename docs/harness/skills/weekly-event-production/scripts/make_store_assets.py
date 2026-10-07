#!/usr/bin/env python3
"""글자 없는 키아트 원본을 스토어 제출 규격으로 잘라 keyart/store/ 에 저장한다.

이미지 모델은 1536×1024·1024×1536·1254×1254 같은 생성 해상도로 만든다. App Store 인앱 이벤트와
Google Play 프로모션 콘텐츠는 1920×1080(16:9)·1080×1920(9:16)·1080×1080(1:1)을 요구하므로,
가운데 기준으로 비율을 맞춰 자르고(cover) 리사이즈한다. 늘리지 않으므로 왜곡이 없다.
스토어 등록 시트(store_submission.md)는 이 사본 경로를 가리켜야 한다.

Usage:
  python make_store_assets.py deliverables/task2-weekly-event/{event_id}/keyart
"""
import os
import sys

from PIL import Image, ImageOps

SPECS = {
    "keyart_16x9.png": (1920, 1080),
    "keyart_9x16.png": (1080, 1920),
    "keyart_1x1.png": (1080, 1080),
}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    keyart = sys.argv[1]
    out = os.path.join(keyart, "store")
    os.makedirs(out, exist_ok=True)
    for name, (w, h) in SPECS.items():
        src = os.path.join(keyart, name)
        if not os.path.exists(src):
            print(f"[skip] {src} 없음", file=sys.stderr)
            continue
        im = Image.open(src).convert("RGB")
        dst = os.path.join(out, name.replace(".png", f"_{w}x{h}.png"))
        ImageOps.fit(im, (w, h), Image.LANCZOS, centering=(0.5, 0.5)).save(dst)
        print(f"{dst}  ({im.width}×{im.height} → {w}×{h})")


if __name__ == "__main__":
    main()
