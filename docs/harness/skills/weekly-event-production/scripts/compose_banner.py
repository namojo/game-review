#!/usr/bin/env python3
"""글자 없는 키아트 위에 언어별 이벤트 문구를 합성해 규격별 PNG 를 만든다.

이미지 생성 모델은 언어마다 글자를 정확히 그리지 못하고, 언어 수만큼 다시 생성하면
비용과 시간이 늘어난다. 그래서 키아트는 한 번만 글자 없이 만들고, 문구는 HTML 로
렌더링해 얹는다. 오탈자는 copy.json 만 고치면 즉시 다시 뽑을 수 있다.

Usage:
  python compose_banner.py --copy copy.json \
      --bg landscape=keyart_16x9.png --bg portrait=keyart_9x16.png --bg square=keyart_1x1.png \
      --locales ko,en,ja --formats landscape,portrait,square --outdir out/
  # --locales 생략 시 copy.json 의 모든 언어, --formats 생략 시 --bg 로 준 규격 전부

copy.json:
  {"accent": "#ff7a1a",
   "locales": {"ko": {"banner": {"title": "...", "subtitle": "...", "period": "10.26 – 11.01", "badge": "기간 한정"},
                      ...그 밖의 필드(notice, push, store)...}, ...}}
"""
import argparse
import base64
import html
import json
import mimetypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "banner_template.html")

FORMATS = {  # 이름: (W, H, 제목 px, 부제 px, 기간 px, 배지 px, 문구 최대 높이 비율)
    "landscape": (1920, 1080, 118, 54, 40, 34, 0.78),  # 16:9 — 인게임 공지·Play 프로모션·App Store 이벤트 카드
    "portrait": (1080, 1920, 124, 56, 42, 36, 0.42),   # 9:16 — App Store 이벤트 상세·SNS 스토리
    "square": (1080, 1080, 96, 46, 36, 30, 0.50),      # 1:1  — SNS 피드·인게임 팝업
}

# 언어별 글꼴. 웹 글꼴을 못 받으면 시스템 글꼴로 대체된다.
FONTS = {
    "ko": ("'Black Han Sans','Apple SD Gothic Neo',sans-serif", "'Apple SD Gothic Neo','Noto Sans KR',sans-serif"),
    "ja": ("'Dela Gothic One','Hiragino Sans',sans-serif", "'Hiragino Sans','Hiragino Kaku Gothic ProN',sans-serif"),
    "zh-TW": ("'Noto Sans TC','PingFang TC',sans-serif", "'PingFang TC','Noto Sans TC',sans-serif"),
    "th": ("'Kanit','Thonburi',sans-serif", "'Kanit','Thonburi',sans-serif"),
}
DEFAULT_FONT = ("'Rubik','Helvetica Neue',Arial,sans-serif", "'Rubik','Helvetica Neue',Arial,sans-serif")


def data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(open(path, "rb").read()).decode()


def build_html(tpl, fmt, lang, c, bg_uri, accent):
    W, H, ts, ss, ps, bs, ratio = FORMATS[fmt]
    tf, bf = FONTS.get(lang, DEFAULT_FONT)
    esc = lambda s: html.escape(s or "").replace("\\n", "<br>").replace("\n", "<br>")
    badge = f'<div class="badge">{esc(c.get("badge"))}</div>' if c.get("badge") else ""
    period = f'<div class="period">{esc(c.get("period"))}</div>' if c.get("period") else ""
    rep = {
        "{{LANG}}": lang, "{{W}}": str(W), "{{H}}": str(H), "{{FORMAT}}": fmt, "{{BG}}": bg_uri,
        "{{ACCENT}}": accent, "{{TITLE_FONT}}": tf, "{{BODY_FONT}}": bf,
        "{{TITLE_SIZE}}": str(ts), "{{SUB_SIZE}}": str(ss), "{{PERIOD_SIZE}}": str(ps),
        "{{BADGE_SIZE}}": str(bs), "{{MAX_COPY_RATIO}}": str(ratio),
        "{{WORD_BREAK}}": {"ko": "keep-all", "ja": "auto-phrase"}.get(lang, "normal"),
        "{{TITLE}}": esc(c.get("title")), "{{SUBTITLE}}": esc(c.get("subtitle")),
        "{{BADGE_HTML}}": badge, "{{PERIOD_HTML}}": period,
    }
    for k, v in rep.items():
        tpl = tpl.replace(k, v)
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--copy", required=True)
    ap.add_argument("--bg", action="append", required=True, help="format=path (여러 번 지정)")
    ap.add_argument("--locales")
    ap.add_argument("--formats")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--prefix", default="banner")
    args = ap.parse_args()

    copy = json.load(open(args.copy, encoding="utf-8"))
    bgs = dict(b.split("=", 1) for b in args.bg)
    for f, p in bgs.items():
        if f not in FORMATS:
            sys.exit(f"알 수 없는 규격: {f} (가능: {', '.join(FORMATS)})")
        if not os.path.exists(p):
            sys.exit(f"키아트 없음: {p}")
    locales = args.locales.split(",") if args.locales else list(copy["locales"])
    formats = args.formats.split(",") if args.formats else list(bgs)
    accent = copy.get("accent", "#ff7a1a")
    tpl = open(TEMPLATE, encoding="utf-8").read()
    os.makedirs(args.outdir, exist_ok=True)

    from playwright.sync_api import sync_playwright
    made = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for fmt in formats:
            W, H = FORMATS[fmt][:2]
            uri = data_uri(bgs[fmt])
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            for lang in locales:
                c = copy["locales"].get(lang)
                c = (c or {}).get("banner", c)  # locales.{lang}.banner 우선, 없으면 locales.{lang}
                if not c:
                    print(f"[warn] copy.json 에 {lang} 없음 — 건너뜀", file=sys.stderr)
                    continue
                page.set_content(build_html(tpl, fmt, lang, c, uri, accent), wait_until="networkidle")
                page.wait_for_selector("body[data-ready='1']", timeout=15000)
                out = os.path.join(args.outdir, f"{args.prefix}_{lang}_{fmt}.png")
                page.screenshot(path=out, full_page=False)
                made.append(out)
            page.close()
        browser.close()
    for m in made:
        print(m)
    print(f"[done] {len(made)}장", file=sys.stderr)


if __name__ == "__main__":
    main()
