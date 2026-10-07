#!/usr/bin/env python3
"""Google Play + App Store 리뷰 수집기 (공개 데이터 기반 PoC용).

- Google Play: google-play-scraper 로 언어별 최신 리뷰를 수집한다.
- App Store: iTunes 고객 리뷰 RSS(국가별 최근 최대 500건)를 수집한다.
- 작성자 이름은 저장하지 않고 해시 별칭으로 바꾼다(공개 사이트 게시 대비).

운영 환경에서는 공식 API(Google Play Developer API reviews.list,
App Store Connect API customerReviews)로 교체한다. 출력 스키마는 동일하게 유지한다.

Usage:
  python fetch_reviews.py --out _workspace/01_collector_reviews_raw.json \
      --days 14 --max-per-source 150
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import urllib.request

GP_APP_ID = "com.primitivebrother.thunder.google"
IOS_APP_ID = "1490207503"

# (lang, country) — Google Play 리뷰는 lang 기준으로 필터된다.
GP_LANGS = [
    ("ko", "kr"), ("en", "us"), ("ja", "jp"), ("zh-TW", "tw"), ("vi", "vn"),
    ("th", "th"), ("id", "id"), ("es", "mx"), ("pt", "br"), ("de", "de"),
    ("fr", "fr"), ("ru", "ru"), ("tr", "tr"),
]
# App Store 국가 → 대표 언어 추정(실제 언어는 분석 단계에서 다시 판별)
IOS_COUNTRIES = {
    "kr": "ko", "us": "en", "jp": "ja", "tw": "zh-TW", "gb": "en", "ca": "en",
    "au": "en", "de": "de", "fr": "fr", "th": "th", "vn": "vi", "id": "id",
    "br": "pt", "mx": "es", "hk": "zh-TW", "ph": "en",
}


def alias(*parts):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]
    return f"user-{h}"


def fetch_google_play(since, max_per_source):
    try:
        from google_play_scraper import Sort, reviews
    except ImportError:
        sys.exit("google-play-scraper 미설치: pip install google-play-scraper")
    out = []
    for lang, country in GP_LANGS:
        token, got = None, []
        while len(got) < max_per_source:
            try:
                batch, token = reviews(GP_APP_ID, lang=lang, country=country,
                                       sort=Sort.NEWEST, count=min(100, max_per_source - len(got)),
                                       continuation_token=token)
            except Exception as e:  # 네트워크/파싱 오류는 해당 언어만 건너뛴다
                print(f"[warn] google_play {lang}: {e}", file=sys.stderr)
                break
            if not batch:
                break
            fresh = [r for r in batch if r["at"] >= since]
            got.extend(fresh)
            if len(fresh) < len(batch) or token is None:
                break  # 기간 밖으로 넘어감
            time.sleep(0.5)
        for r in got:
            out.append({
                "id": f"gp-{lang}-{hashlib.sha1(r['reviewId'].encode()).hexdigest()[:10]}",
                "source_review_id": r["reviewId"],
                "store": "google_play",
                "lang": lang,
                "country": country,
                "rating": r["score"],
                "title": "",
                "text": (r["content"] or "").strip(),
                "author": alias(r["userName"], r["reviewId"]),
                "created_at": r["at"].isoformat(),
                "app_version": r.get("reviewCreatedVersion") or r.get("appVersion"),
                "thumbs_up": r.get("thumbsUpCount", 0),
                "existing_reply": ({"text": r["replyContent"],
                                    "at": r["repliedAt"].isoformat() if r.get("repliedAt") else None}
                                   if r.get("replyContent") else None),
            })
        print(f"[info] google_play {lang}: {len(got)}", file=sys.stderr)
    return out


def fetch_app_store(since, max_per_source):
    out = []
    for country, lang in IOS_COUNTRIES.items():
        got = []
        for page in range(1, 11):
            url = (f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}"
                   f"/id={IOS_APP_ID}/sortby=mostrecent/json")
            try:
                with urllib.request.urlopen(url, timeout=20) as resp:
                    feed = json.load(resp).get("feed", {})
            except Exception as e:
                print(f"[warn] app_store {country} p{page}: {e}", file=sys.stderr)
                break
            entries = [e for e in feed.get("entry", []) if isinstance(e, dict) and "im:rating" in e]
            if not entries:
                break
            stop = False
            for e in entries:
                at = dt.datetime.fromisoformat(e["updated"]["label"]).astimezone(dt.timezone.utc).replace(tzinfo=None)
                if at < since:
                    stop = True
                    continue
                got.append((e, at))
            if stop or len(got) >= max_per_source:
                break
            time.sleep(0.3)
        for e, at in got[:max_per_source]:
            rid = e["id"]["label"]
            out.append({
                "id": f"as-{country}-{hashlib.sha1(rid.encode()).hexdigest()[:10]}",
                "source_review_id": rid,
                "store": "app_store",
                "lang": lang,
                "country": country,
                "rating": int(e["im:rating"]["label"]),
                "title": e.get("title", {}).get("label", "").strip(),
                "text": e.get("content", {}).get("label", "").strip(),
                "author": alias(e.get("author", {}).get("name", {}).get("label", ""), rid),
                "created_at": at.isoformat(),
                "app_version": e.get("im:version", {}).get("label"),
                "thumbs_up": int(e.get("im:voteSum", {}).get("label", 0) or 0),
                "existing_reply": None,  # RSS에는 개발자 답변이 없다
            })
        if got:
            print(f"[info] app_store {country}: {min(len(got), max_per_source)}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-per-source", type=int, default=150)
    ap.add_argument("--now", help="기준 시각 ISO(재현용). 기본: 현재 UTC")
    ap.add_argument("--skip-ios", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    since = now - dt.timedelta(days=args.days)

    rows = fetch_google_play(since, args.max_per_source)
    if not args.skip_ios:
        rows += fetch_app_store(since, args.max_per_source)
    seen, uniq = set(), []
    for r in rows:
        if r["id"] in seen or not r["text"]:
            continue
        seen.add(r["id"])
        uniq.append(r)
    uniq.sort(key=lambda r: r["created_at"], reverse=True)

    meta = {
        "app": {"google_play": GP_APP_ID, "app_store": IOS_APP_ID,
                "title_ko": "원시인 형님들 키우기 : 문명 x 방치형 RPG", "publisher": "T.G Inc (Thundergames)"},
        "collected_at": now.isoformat(timespec="seconds") + "Z",
        "window_days": args.days,
        "count": len(uniq),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "reviews": uniq}, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
