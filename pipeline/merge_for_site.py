#!/usr/bin/env python3
"""수집 원본 + 분석 초안을 합쳐 웹서비스가 읽을 data/reviews.json 을 만든다.

웹서비스는 이 파일 하나만 읽는다(정적 호스팅). 필드 계약은 review-web-service 스킬의
references/data-contract.md 와 같아야 한다.

--base 를 주면 기존 reviews.json 에 누적 병합한다(일일 운영용).
  - 기존 리뷰는 id 로 유지한다. 기존 ai 는 바꾸지 않는다(이번 초안에 같은 id 가 있을 때만 갱신).
  - 이번 수집분에 다시 나온 리뷰는 원문·별점·공감 수·기존 답변·status 만 최신으로 갱신한다.
  - 사용자가 리뷰를 고쳐 text·title·rating 이 바뀌면 content_changed_at(수집 기준 시각)을 붙인다.
    make_batches.py --exclude-existing 이 이런 리뷰를 다시 배치에 넣으므로 ai 는 새 초안으로 바뀐다.
    화면은 content_changed_at 보다 먼저 저장된 검수 상태를 버리고 '내용 변경됨'으로 표시한다.
  - 새 id 만 추가한다. stats 는 합친 전체 기준으로 다시 계산한다.
  - --keep-days N 을 주면 수집 기준 시각에서 N일보다 오래된 리뷰만 잘라낸다(기본: 자르지 않음).
--base 가 없으면 이번 수집분만으로 만든다(처음 만들 때, 데모 재생성).

Usage:
  python merge_for_site.py --workspace _workspace --out game-review/data/reviews.json
  python merge_for_site.py --workspace work --base data/reviews.json --out data/reviews.json [--keep-days 90]
"""
import argparse
import datetime as dt
import glob
import json
import os
from collections import Counter, defaultdict

KEEP = ("id", "source_review_id", "store", "lang", "country", "rating", "title", "text",
        "author", "created_at", "app_version", "thumbs_up", "existing_reply")
# 다시 수집될 때 최신 값으로 바꾸는 필드(사용자가 리뷰를 고치거나 운영자가 답변을 달 수 있다)
REFRESH = ("rating", "title", "text", "app_version", "thumbs_up", "existing_reply")
AI = ("category", "sentiment", "priority", "route_to", "needs_cs", "summary_ko", "detected_lang",
      "translation_ko", "reply", "reply_ko", "reply_strategy", "confidence", "flags")


def content_key(r):
    """리뷰 내용이 바뀌었는지 비교하는 키. make_batches.py·draft_replies.py 와 같은 규칙."""
    return ((r.get("text") or "").strip(), (r.get("title") or "").strip(), int(r.get("rating") or 0))


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "")) if s else None


def load_drafts(workspace):
    drafts, templates = {}, defaultdict(list)
    for p in sorted(glob.glob(os.path.join(workspace, "03_analyst_drafts_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        for x in d.get("drafts", []):
            drafts[x["id"]] = x
        for lang, variants in (d.get("templates") or {}).items():
            for v in variants:
                if v not in templates[lang]:
                    templates[lang].append(v)
    return drafts, templates


def compute_stats(rows, window_days):
    with_ai = [r for r in rows if r["ai"]]
    daily = Counter(r["created_at"][:10] for r in rows)
    cat_rating = defaultdict(list)
    for r in with_ai:
        cat_rating[r["ai"]["category"]].append(r["rating"])
    return {
        "total": len(rows),
        "with_ai": len(with_ai),
        "missing_ai": len(rows) - len(with_ai),
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "avg_rating": round(sum(r["rating"] for r in rows) / max(1, len(rows)), 2),
        "by_store": Counter(r["store"] for r in rows),
        "by_lang": Counter((r["ai"] or {}).get("detected_lang") or r["lang"] for r in rows),
        "by_rating": Counter(str(r["rating"]) for r in rows),
        "by_category": Counter(r["ai"]["category"] for r in with_ai),
        "by_priority": Counter(r["ai"]["priority"] for r in with_ai),
        "by_route": Counter(r["ai"]["route_to"] for r in with_ai),
        "needs_cs": sum(1 for r in with_ai if r["ai"]["needs_cs"]),
        "category_avg_rating": {k: round(sum(v) / len(v), 2) for k, v in cat_rating.items()},
        "daily": dict(sorted(daily.items())),
        "per_day_avg": round(len(rows) / max(1, window_days or len(daily)), 1),  # 수집 기간 기준(리뷰 없는 날 포함)
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default="_workspace")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", help="누적 병합할 기존 reviews.json (없으면 이번 수집분만으로 만든다)")
    ap.add_argument("--keep-days", type=int, help="--base 와 함께: 수집 기준 시각에서 N일보다 오래된 리뷰를 잘라낸다")
    args = ap.parse_args()

    raw = json.load(open(os.path.join(args.workspace, "01_collector_reviews_raw.json"), encoding="utf-8"))
    drafts, templates = load_drafts(args.workspace)
    meta = dict(raw["meta"])
    window_days = meta.get("window_days")

    base_rows, base = {}, None
    if args.base and os.path.exists(args.base):
        base = json.load(open(args.base, encoding="utf-8"))
        base_rows = {r["id"]: r for r in base.get("reviews", [])}
        merged_tpl = defaultdict(list)
        for lang, variants in (base.get("templates") or {}).items():
            merged_tpl[lang].extend(variants)
        for lang, variants in templates.items():
            for v in variants:
                if v not in merged_tpl[lang]:
                    merged_tpl[lang].append(v)
        templates = merged_tpl
    elif args.base:
        print(f"[warn] --base {args.base} 가 없어 이번 수집분만으로 만든다")

    rows, added, refreshed, changed, reanalyzed = {}, 0, 0, 0, 0
    for r in base_rows.values():
        rows[r["id"]] = dict(r)
    for r in raw["reviews"]:
        x = drafts.get(r["id"])
        if r["id"] in rows:
            row = rows[r["id"]]
            if content_key(row) != content_key(r):
                row["content_changed_at"] = meta.get("collected_at")
                changed += 1
            for k in REFRESH:
                row[k] = r.get(k)
            if x:  # 이번에 다시 분석한 경우에만 ai 를 바꾼다
                row["ai"] = {k: x.get(k) for k in AI}
                reanalyzed += 1
            refreshed += 1
        else:
            row = {k: r.get(k) for k in KEEP}
            row["ai"] = {k: x.get(k) for k in AI} if x else None
            rows[r["id"]] = row
            added += 1
        row["status"] = "replied" if row.get("existing_reply") else "pending"

    if base is not None:
        # 누적 기간: 기존 데이터의 시작 시점 ~ 이번 수집 기준 시각
        now = parse_ts(meta.get("collected_at")) or dt.datetime.utcnow()
        b_meta = base.get("meta", {})
        b_start = (parse_ts(b_meta.get("collected_at")) or now) - dt.timedelta(days=b_meta.get("window_days") or 0)
        start = min(b_start, now - dt.timedelta(days=window_days or 0))
        if args.keep_days:
            cutoff = now - dt.timedelta(days=args.keep_days)
            before = len(rows)
            rows = {k: v for k, v in rows.items() if parse_ts(v["created_at"]) >= cutoff}
            print(f"[info] --keep-days {args.keep_days}: {before - len(rows)}건 잘라냄")
            start = max(start, cutoff)
        window_days = max(1, round((now - start).total_seconds() / 86400))
        meta["window_days"] = window_days

    out_rows = sorted(rows.values(), key=lambda r: r["created_at"], reverse=True)
    stats = compute_stats(out_rows, window_days)
    meta["count"] = len(out_rows)
    out = {
        "meta": meta | {"merged_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"},
        "stats": stats,
        "templates": templates,
        "reviews": out_rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    summary = {k: stats[k] for k in ("total", "with_ai", "missing_ai", "pending", "needs_cs")}
    if base is not None:
        summary |= {"base": len(base_rows), "added": added, "refreshed": refreshed,
                    "content_changed": changed, "reanalyzed": reanalyzed}
        if changed > reanalyzed:
            print(f"[warn] 내용이 바뀌었는데 새 초안이 없는 리뷰 {changed - reanalyzed}건 — 이전 ai 가 남는다. "
                  "make_batches.py --exclude-existing 으로 다시 배치를 만들었는지 확인한다")
    print(json.dumps(summary, ensure_ascii=False))
    if stats["missing_ai"]:
        print(f"[warn] AI 초안이 없는 리뷰 {stats['missing_ai']}건 — 화면에는 '분석 대기'로 표시된다")


if __name__ == "__main__":
    main()
