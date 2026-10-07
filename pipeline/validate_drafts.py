#!/usr/bin/env python3
"""답변 초안 파일이 배치와 스키마 규칙에 맞는지 검사한다.

오류(error)가 하나라도 있으면 종료 코드 1을 반환한다. 경고(warn)는 품질 신호로만 보고한다.

Usage:
  python validate_drafts.py --batch _workspace/02_batches/batch_ko-1.json \
      --drafts _workspace/03_analyst_drafts_ko-1.json
  python validate_drafts.py --all _workspace   # 02_batches/manifest.json 기준 전체 검사
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

CATEGORIES = {"praise", "ads", "monetization", "purchase_issue", "bug", "account",
              "reward_issue", "balance", "content_request", "performance", "other"}
SENTIMENTS = {"positive", "neutral", "negative", "mixed"}
PRIORITIES = {"P1", "P2", "P3"}
ROUTES = {"cs", "dev", "bm", "planning", "none"}
STRATEGIES = {"template", "personalized"}
FLAGS = {"profanity", "refund_request", "legal_threat", "minor_user", "spam",
         "competitor_mention", "needs_human", "personal_info", "update_related"}
LIMIT = {"google_play": 350, "app_store": 5970}
SCRIPT = {  # 문자 체계로 답변 언어를 확인할 수 있는 언어
    "ko": r"[가-힣]", "ja": r"[ぁ-んァ-ン]", "zh-TW": r"[一-鿿]", "zh-CN": r"[一-鿿]",
    "th": r"[฀-๿]", "ru": r"[А-Яа-яЁё]",
}


def check(batch_path, drafts_path):
    errors, warns = [], []
    batch = json.load(open(batch_path, encoding="utf-8"))
    src = {r["id"]: r for r in batch["reviews"]}
    try:
        d = json.load(open(drafts_path, encoding="utf-8"))
    except FileNotFoundError:
        return [f"초안 파일 없음: {drafts_path}"], [], 0
    except json.JSONDecodeError as e:
        return [f"JSON 파싱 실패: {e}"], [], 0
    drafts = d.get("drafts", [])
    ids = [x.get("id") for x in drafts]
    missing = set(src) - set(ids)
    extra = set(ids) - set(src)
    dup = [i for i, c in Counter(ids).items() if c > 1]
    if missing:
        errors.append(f"누락 {len(missing)}건: {sorted(missing)[:5]}")
    if extra:
        errors.append(f"배치에 없는 id {len(extra)}건: {sorted(extra)[:5]}")
    if dup:
        errors.append(f"중복 id: {dup[:5]}")

    for x in drafts:
        i = x.get("id")
        r = src.get(i)
        if not r:
            continue
        def e(msg):
            errors.append(f"{i}: {msg}")
        if x.get("category") not in CATEGORIES:
            e(f"category={x.get('category')}")
        if x.get("sentiment") not in SENTIMENTS:
            e(f"sentiment={x.get('sentiment')}")
        if x.get("priority") not in PRIORITIES:
            e(f"priority={x.get('priority')}")
        if x.get("route_to") not in ROUTES:
            e(f"route_to={x.get('route_to')}")
        if x.get("reply_strategy") not in STRATEGIES:
            e(f"reply_strategy={x.get('reply_strategy')}")
        if not isinstance(x.get("needs_cs"), bool):
            e("needs_cs 는 true/false")
        bad_flags = set(x.get("flags") or []) - FLAGS
        if bad_flags:
            e(f"알 수 없는 flags {bad_flags}")
        conf = x.get("confidence")
        if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            e(f"confidence={conf}")
        reply = (x.get("reply") or "").strip()
        lim = LIMIT[r["store"]]
        if len(reply) < 15:
            e("reply 가 비었거나 너무 짧음")
        elif len(reply) > lim:
            e(f"reply {len(reply)}자 > {r['store']} 한도 {lim}자")
        if not (x.get("summary_ko") or "").strip():
            e("summary_ko 없음")
        lang = x.get("detected_lang") or r["lang"]
        if lang != "ko":
            if not (x.get("translation_ko") or "").strip():
                e("비한국어 리뷰인데 translation_ko 없음")
            if not (x.get("reply_ko") or "").strip():
                e("비한국어 리뷰인데 reply_ko 없음")
        if x.get("category") in {"purchase_issue", "account"} and not x.get("needs_cs"):
            warns.append(f"{i}: {x.get('category')} 인데 needs_cs=false")
        if x.get("category") in {"purchase_issue", "account"} and x.get("priority") != "P1":
            warns.append(f"{i}: {x.get('category')} 는 보통 P1")
        if "Bow" not in reply and "보우" not in reply and r["store"] == "google_play":
            warns.append(f"{i}: 페르소나(GM Mr. Bow) 표기가 없음")
        # 답변 언어 = detected_lang. 문자 체계가 뚜렷한 언어는 문자로 확인하고, 그 외에는 경고로만 알린다.
        script = SCRIPT.get(lang)
        if script and not re.search(script, reply):
            e(f"detected_lang={lang} 인데 답변에 해당 문자가 없음 — 답변 언어 확인")
        if not script and re.search(SCRIPT["ko"] + "|" + SCRIPT["ru"] + "|" + SCRIPT["th"], reply):
            e(f"detected_lang={lang} 인데 답변이 다른 문자 체계로 쓰였음")
        if lang != r["lang"]:
            warns.append(f"{i}: 배치 언어 {r['lang']} ≠ 리뷰 언어 {lang} — 답변이 {lang} 로 쓰였는지 확인")

    pers = [x.get("reply", "").strip() for x in drafts if x.get("reply_strategy") == "personalized"]
    if pers:
        top = Counter(pers).most_common(1)[0][1]
        if top > max(2, len(pers) * 0.2):
            warns.append(f"맞춤 답변 중 같은 문구가 {top}번 반복됨 — 리뷰별 맞춤성이 낮다")
    return errors, warns, len(drafts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch")
    ap.add_argument("--drafts")
    ap.add_argument("--all", help="_workspace 경로")
    args = ap.parse_args()

    pairs = []
    if args.all:
        man = json.load(open(os.path.join(args.all, "02_batches", "manifest.json"), encoding="utf-8"))
        for m in man:
            pairs.append((m["path"], os.path.join(args.all, f"03_analyst_drafts_{m['batch']}.json")))
    else:
        pairs.append((args.batch, args.drafts))

    total_err = 0
    for b, dpath in pairs:
        errors, warns, n = check(b, dpath)
        total_err += len(errors)
        status = "OK" if not errors else "FAIL"
        print(f"[{status}] {os.path.basename(dpath)} — 초안 {n}건, 오류 {len(errors)}, 경고 {len(warns)}")
        for m in errors[:30]:
            print("  ERROR", m)
        for m in warns[:15]:
            print("  warn ", m)
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
