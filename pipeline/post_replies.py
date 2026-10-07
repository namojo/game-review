#!/usr/bin/env python3
"""검수 화면에서 내려받은 승인 답변(JSON)을 Google Play·App Store 공식 API로 등록한다.

기본은 --dry-run 이다. 실제 등록은 --execute 를 붙였을 때만 한다.
공개 답변은 되돌리기 어렵고 브랜드에 바로 노출되므로, 사람이 승인한 파일만 받는다.

입력 형식(검수 화면 "승인 답변 JSON"과 같다):
  [{"store": "google_play", "source_review_id": "...", "lang": "ko", "rating": 1,
    "reply": "...", "approved_at": "2026-10-07T01:23:45.000Z", "edited": false}, ...]

인증(환경 변수, 저장소에 넣지 않는다 — GitHub Actions Secrets 나 비밀 관리자에 둔다)
  Google Play : GOOGLE_APPLICATION_CREDENTIALS=서비스 계정 JSON 경로
                (Play Console [사용자 및 권한]에서 이 앱의 "리뷰 답변" 권한 필요)
  App Store   : ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY_PATH(.p8 경로)

Usage:
  python pipeline/post_replies.py approved_replies.json                 # 미리 보기(dry-run)
  python pipeline/post_replies.py approved_replies.json --execute       # 실제 등록
  python pipeline/post_replies.py approved_replies.json --execute --store google_play --log posted.jsonl
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

GP_PACKAGE = "com.primitivebrother.thunder.google"
LIMIT = {"google_play": 350, "app_store": 5970}
ASC_API = "https://api.appstoreconnect.apple.com/v1/customerReviewResponses"


def load(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        sys.exit("입력은 승인 답변 객체의 배열이어야 한다(검수 화면의 '승인 답변 JSON').")
    return rows


def check(row):
    """등록하면 안 되는 이유를 돌려준다. 문제가 없으면 None."""
    store = row.get("store")
    if store not in LIMIT:
        return f"알 수 없는 store={store!r}"
    if not (row.get("source_review_id") or "").strip():
        return "source_review_id 없음(붙여넣은 임시 리뷰는 스토어에 등록할 수 없다)"
    reply = (row.get("reply") or "").strip()
    if not reply:
        return "reply 가 비어 있음"
    if len(reply) > LIMIT[store]:
        return f"reply {len(reply)}자 > {store} 한도 {LIMIT[store]}자"
    if not row.get("approved_at"):
        return "approved_at 없음(승인되지 않은 답변)"
    return None


class GooglePlay:
    def __init__(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            sys.exit("google-api-python-client 미설치: pip install -r pipeline/requirements.txt")
        key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not key or not os.path.exists(key):
            sys.exit("GOOGLE_APPLICATION_CREDENTIALS 에 서비스 계정 JSON 경로를 지정한다.")
        creds = service_account.Credentials.from_service_account_file(
            key, scopes=["https://www.googleapis.com/auth/androidpublisher"])
        self.svc = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)

    def post(self, row):
        # 기존 답변이 있으면 덮어쓴다(Play 동작). HTML 태그는 Play 가 지운다.
        res = self.svc.reviews().reply(packageName=GP_PACKAGE, reviewId=row["source_review_id"],
                                       body={"replyText": row["reply"].strip()}).execute()
        return res.get("result", {}).get("lastEdited", {})


class AppStore:
    def __init__(self):
        try:
            import jwt  # PyJWT[crypto]
            import requests
        except ImportError:
            sys.exit("PyJWT·requests 미설치: pip install -r pipeline/requirements.txt")
        self.jwt, self.requests = jwt, requests
        self.key_id = os.environ.get("ASC_KEY_ID")
        self.issuer = os.environ.get("ASC_ISSUER_ID")
        path = os.environ.get("ASC_PRIVATE_KEY_PATH")
        if not (self.key_id and self.issuer and path and os.path.exists(path)):
            sys.exit("ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY_PATH(.p8) 를 지정한다.")
        with open(path, encoding="utf-8") as f:
            self.private_key = f.read()
        self._token, self._exp = None, 0

    def token(self):
        now = int(time.time())
        if not self._token or now > self._exp - 60:
            self._exp = now + 15 * 60  # Apple 은 20분 이하 토큰만 받는다
            self._token = self.jwt.encode({"iss": self.issuer, "iat": now, "exp": self._exp, "aud": "appstoreconnect-v1"},
                                          self.private_key, algorithm="ES256", headers={"kid": self.key_id, "typ": "JWT"})
        return self._token

    def post(self, row):
        # 주의: PoC 데이터의 App Store ID 는 iTunes RSS 의 리뷰 ID 다. 운영에서는
        # App Store Connect API(customerReviews)로 수집한 ID 를 써야 한다.
        body = {"data": {"type": "customerReviewResponses",
                         "attributes": {"responseBody": row["reply"].strip()},
                         "relationships": {"review": {"data": {"type": "customerReviews", "id": row["source_review_id"]}}}}}
        r = self.requests.post(ASC_API, json=body, timeout=30,
                               headers={"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"})
        if r.status_code >= 300:
            raise RuntimeError(f"App Store Connect {r.status_code}: {r.text[:300]}")
        return r.json().get("data", {}).get("id")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("approved", help="검수 화면에서 내려받은 승인 답변 JSON")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="등록하지 않고 대상만 보여준다(기본)")
    mode.add_argument("--execute", action="store_true", help="실제로 등록한다")
    ap.add_argument("--store", choices=sorted(LIMIT), help="한 스토어만 등록")
    ap.add_argument("--interval", type=float, default=1.0, help="요청 사이 대기(초). Play API 할당량에 맞춘다")
    ap.add_argument("--log", default="posted_replies.jsonl", help="등록 결과 기록 파일(JSON Lines)")
    args = ap.parse_args()

    rows = [r for r in load(args.approved) if not args.store or r.get("store") == args.store]
    ok_rows, skipped = [], []
    for r in rows:
        why = check(r)
        (skipped.append((r, why)) if why else ok_rows.append(r))

    print(f"승인 답변 {len(rows)}건 — 등록 대상 {len(ok_rows)}건, 제외 {len(skipped)}건")
    for r, why in skipped:
        print(f"  제외 {r.get('store')}:{r.get('source_review_id') or '-'} — {why}")

    if not args.execute:
        for r in ok_rows:
            preview = r["reply"].strip().replace("\n", " ")
            print(f"  [dry-run] {r['store']:11s} {r['source_review_id'][:36]:36s} {len(r['reply'].strip()):4d}자 {preview[:60]}")
        print("\n미리 보기만 했다. 실제로 등록하려면 --execute 를 붙인다.")
        return

    clients = {}
    if any(r["store"] == "google_play" for r in ok_rows):
        clients["google_play"] = GooglePlay()
    if any(r["store"] == "app_store" for r in ok_rows):
        clients["app_store"] = AppStore()

    posted = failed = 0
    with open(args.log, "a", encoding="utf-8") as log:
        for i, r in enumerate(ok_rows):
            rec = {"store": r["store"], "source_review_id": r["source_review_id"], "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            try:
                rec["result"] = clients[r["store"]].post(r)
                rec["ok"] = True
                posted += 1
                print(f"  등록 {r['store']}:{r['source_review_id']}")
            except Exception as e:  # 한 건 실패가 나머지 등록을 막지 않게 한다
                rec["ok"], rec["error"] = False, str(e)[:500]
                failed += 1
                print(f"  실패 {r['store']}:{r['source_review_id']} — {e}", file=sys.stderr)
            log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            if i < len(ok_rows) - 1:
                time.sleep(args.interval)
    print(f"\n등록 {posted}건, 실패 {failed}건. 기록: {args.log}")
    print("검수 화면에서 등록한 리뷰에 '등록 완료 표시'를 누른다(운영 전환 후에는 DB 상태로 자동 반영).")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
