#!/usr/bin/env python3
"""수집한 리뷰를 사전 분류(route)하고 언어 그룹별 배치 파일로 나눈다.

route
  - template : 별점 4~5, 15자 이하, 문제 키워드 없음 → 언어별 템플릿 변형으로 답변(비용 절감)
  - llm      : 그 외 → 개별 맞춤 분류·번역·답변

배치는 같은 언어끼리 묶는다. 한 에이전트가 한 언어의 말투를 일관되게 유지하기 위해서다.
각 배치에는 해당 언어의 기존 운영자 답변 예시(style_examples)를 넣어 페르소나를 맞춘다.

--exclude-existing 에 기존 reviews.json 을 주면 이미 ai 가 있고 내용(text·title·rating)이 그대로인 id 는
배치에서 뺀다. 사용자가 리뷰를 고쳤으면(예: 별점 5 → 1, "결제했는데 아이템이 안 들어왔어요") 다시 분석한다.
style_examples 는 제외한 리뷰까지 포함해 뽑는다(기존 운영 답변 예시를 잃지 않기 위해).

Usage:
  python make_batches.py --raw _workspace/01_collector_reviews_raw.json \
      --outdir _workspace/02_batches --max-batch 75
  python make_batches.py --raw work/01_collector_reviews_raw.json --outdir work/02_batches \
      --exclude-existing data/reviews.json
"""
import argparse
import json
import os
import re
from collections import defaultdict

ISSUE_KEYWORDS = re.compile(
    r"광고|결제|현질|환불|버그|오류|튕|렉|계정|복구|보상|안\s?돼|안됨|안되|느려|"
    r"ad|ads|bug|crash|refund|pay|purchase|account|lag|reward|"
    r"anúncio|propaganda|bug|erro|реклам|баг|ошиб|iklan|anuncio|โฆษณา|広告|課金|廣告",
    re.I,
)

# 언어 그룹: 리뷰 수가 적은 언어는 묶는다.
GROUPS = {
    "ko": ["ko"],
    "en": ["en"],
    "pt": ["pt"],
    "eu": ["ru", "tr", "fr", "de", "it"],
    "asia_es": ["id", "es", "th", "vi", "ja", "zh-TW", "zh-CN"],
}


def content_key(r):
    """리뷰 내용이 바뀌었는지 비교하는 키. merge_for_site.py·draft_replies.py 와 같은 규칙."""
    return ((r.get("text") or "").strip(), (r.get("title") or "").strip(), int(r.get("rating") or 0))


def route(r):
    t = r["text"]
    if r["rating"] >= 4 and len(t) <= 15 and not ISSUE_KEYWORDS.search(t + " " + r.get("title", "")):
        return "template"
    return "llm"


def group_of(lang):
    for g, langs in GROUPS.items():
        if lang in langs:
            return g
    return "asia_es"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--max-batch", type=int, default=75)
    ap.add_argument("--exclude-existing", help="기존 reviews.json — 이미 ai 가 있는 id 는 배치에서 뺀다")
    args = ap.parse_args()

    data = json.load(open(args.raw, encoding="utf-8"))
    reviews = data["reviews"]
    os.makedirs(args.outdir, exist_ok=True)

    done = set()
    if args.exclude_existing and os.path.exists(args.exclude_existing):
        existing = json.load(open(args.exclude_existing, encoding="utf-8"))
        done = {r["id"]: content_key(r) for r in existing.get("reviews", []) if r.get("ai")}

    by_group, all_by_group = defaultdict(list), defaultdict(list)
    for r in reviews:
        r["route"] = route(r)
        all_by_group[group_of(r["lang"])].append(r)
        if done.get(r["id"]) != content_key(r):
            by_group[group_of(r["lang"])].append(r)
    if done:
        same = sum(1 for r in reviews if done.get(r["id"]) == content_key(r))
        changed = sum(1 for r in reviews if r["id"] in done and done[r["id"]] != content_key(r))
        print(f"이미 분석한 리뷰 {same}건 제외, 내용이 바뀐 리뷰 {changed}건 재분석, 새 리뷰 {sum(len(v) for v in by_group.values()) - changed}건")

    manifest = []
    for g, items in by_group.items():
        langs = sorted({r["lang"] for r in items})
        # 언어별 기존 답변 예시(최대 4개, 서로 다른 문구 우선)
        examples = {}
        for lang in langs:
            seen, ex = set(), []
            for r in all_by_group[g]:
                rep = (r.get("existing_reply") or {}).get("text")
                if r["lang"] == lang and rep and rep[:40] not in seen:
                    seen.add(rep[:40])
                    ex.append({"rating": r["rating"], "review": r["text"][:120], "reply": rep})
                if len(ex) >= 4:
                    break
            examples[lang] = ex
        # 크기를 고르게 나눈다(예: 98건, 상한 75 → 49+49). 자투리 소형 배치를 만들지 않기 위해서다.
        n_chunks = -(-len(items) // args.max_batch)
        size = -(-len(items) // n_chunks)
        chunks = [items[i:i + size] for i in range(0, len(items), size)]
        for n, chunk in enumerate(chunks, 1):
            name = f"{g}-{n}"
            path = os.path.join(args.outdir, f"batch_{name}.json")
            slim = [{k: r[k] for k in ("id", "store", "lang", "country", "rating", "title",
                                         "text", "app_version", "created_at", "route")}
                    | {"existing_reply": (r.get("existing_reply") or {}).get("text")}
                    for r in chunk]
            json.dump({"batch": name, "langs": langs, "style_examples": examples,
                       "reviews": slim}, open(path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            manifest.append({"batch": name, "path": path, "count": len(chunk),
                             "llm": sum(1 for r in chunk if r["route"] == "llm"),
                             "template": sum(1 for r in chunk if r["route"] == "template"),
                             "langs": sorted({r["lang"] for r in chunk})})
    json.dump(manifest, open(os.path.join(args.outdir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for m in manifest:
        print(f"{m['batch']:10s} {m['count']:4d}건 (llm {m['llm']}, template {m['template']}) {','.join(m['langs'])}")


if __name__ == "__main__":
    main()
