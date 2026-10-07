# 운영 파이프라인

검수 화면(`index.html`)이 읽는 `data/reviews.json` 을 만들고, 승인한 답변을 스토어에 등록하는 스크립트입니다. 저장소 루트에서 실행합니다.

```
fetch_reviews.py   →  make_batches.py  →  draft_replies.py  →  validate_drafts.py  →  merge_for_site.py  →  (검수 화면)  →  post_replies.py
   수집                 사전 분류·배치      Claude 분류·번역·초안    규칙 검사               사이트 데이터            사람이 승인         스토어 등록
```

| 파일 | 하는 일 | 필요한 것 |
|---|---|---|
| `fetch_reviews.py` | Google Play(google-play-scraper)·App Store(iTunes RSS) 리뷰 수집. 작성자 이름은 해시 별칭으로 바꾼다 | 없음 |
| `make_batches.py` | 짧은 칭찬은 `template`, 나머지는 `llm` 으로 사전 분류하고 언어 그룹별 배치로 나눈다. 언어별 실제 운영 답변을 `style_examples` 로 넣는다. `--exclude-existing data/reviews.json` 이면 이미 분석했고 내용(text·title·rating)이 그대로인 id 는 뺀다. 작성자가 리뷰를 고쳤으면 다시 분석한다 | 없음 |
| `draft_replies.py` | 분류·번역(Claude Haiku 4.5) → 언어별 템플릿 3종 → 맞춤 답변(Claude Sonnet 5.5). 모든 응답을 JSON 스키마로 받는다 | `ANTHROPIC_API_KEY` |
| `validate_drafts.py` | id 누락·중복, 스키마, 350자 한도, 번역 누락, 답변 언어(문자 체계) 검사. 오류가 있으면 종료 코드 1 | 없음 |
| `merge_for_site.py` | 원본 + 초안 → `data/reviews.json`(통계 포함). `--base data/reviews.json` 이면 기존 리뷰·ai 를 유지하고 새 id 만 추가하는 누적 병합, `--keep-days N` 으로 보존 기간 지정 | 없음 |
| `post_replies.py` | 검수 화면의 "승인 답변 JSON"을 공식 API로 등록. **기본은 미리 보기**, `--execute` 때만 등록 | 서비스 계정, ASC 키 |
| `prompts/` | `draft_replies.py` 의 시스템 프롬프트(분류 체계, 답변 지침). 말투를 바꾸려면 여기를 고친다 | |

`fetch_reviews.py`, `make_batches.py`, `validate_drafts.py`, `merge_for_site.py` 와 `prompts/` 는 컨설팅 작업 폴더의 `review-response-ops` 스킬에 있는 원본의 복사본입니다. 원본을 고치면 이쪽도 같이 바꿉니다.

## 한 번 돌려 보기

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r pipeline/requirements.txt
export ANTHROPIC_API_KEY=...            # 저장소·코드에 넣지 않는다

python pipeline/fetch_reviews.py --out work/01_collector_reviews_raw.json --days 2 --max-per-source 200
python pipeline/make_batches.py --raw work/01_collector_reviews_raw.json --outdir work/02_batches
python pipeline/draft_replies.py --manifest work/02_batches/manifest.json --outdir work --dry-run   # 호출 수만 확인
python pipeline/draft_replies.py --manifest work/02_batches/manifest.json --outdir work
python pipeline/validate_drafts.py --all work
python pipeline/merge_for_site.py --workspace work --out data/reviews.json
python3 -m http.server 8765             # http://localhost:8765/ 에서 검수
```

- 위는 처음 만들 때(또는 데모 재생성)입니다. **이미 `data/reviews.json` 이 있으면 누적 병합**합니다. 이미 분석한 리뷰는 다시 과금하지 않습니다.

```bash
python pipeline/fetch_reviews.py --out work/01_collector_reviews_raw.json --days 2 --max-per-source 200
python pipeline/make_batches.py --raw work/01_collector_reviews_raw.json --outdir work/02_batches --exclude-existing data/reviews.json
python pipeline/draft_replies.py --manifest work/02_batches/manifest.json --outdir work --existing data/reviews.json
python pipeline/validate_drafts.py --all work
python pipeline/merge_for_site.py --workspace work --base data/reviews.json --out data/reviews.json --keep-days 180
```

- 시험할 때는 `--limit 5` 로 배치마다 5건만 처리할 수 있습니다.
- 일부 리뷰가 실패하면 `draft_replies.py` 는 종료 코드 2로 끝나고, 그 리뷰는 화면에 "분석 대기"로 남습니다. 다시 실행하면 채웁니다. 키·권한·모델·연결 오류는 바로 멈춥니다.
- 맞춤 답변이 350자를 넘으면 더 짧게 최대 두 번 다시 요청합니다.
- `confidence < 0.6`, 환불 요청, 법적 대응 언급, 미성년자+결제 조합에는 `needs_human` 을 코드에서 한 번 더 붙입니다.

## 답변 등록

```bash
# 검수 화면 [검수 인박스] → "승인 답변 N건 JSON" 으로 내려받은 파일
python pipeline/post_replies.py approved_replies_202610071030.json              # 미리 보기
export GOOGLE_APPLICATION_CREDENTIALS=/secure/play-service-account.json
export ASC_KEY_ID=... ASC_ISSUER_ID=... ASC_PRIVATE_KEY_PATH=/secure/AuthKey.p8
python pipeline/post_replies.py approved_replies_202610071030.json --execute    # 실제 등록
```

- Google Play: Android Publisher API `reviews.reply`. 350자를 넘으면 거부되고, 기존 답변은 덮어씁니다. 목록 API는 **최근 1주** 리뷰만 주므로 매일 수집해야 놓치지 않습니다.
- App Store: App Store Connect API `customerReviewResponses`. PoC 데이터의 App Store ID는 iTunes RSS 값이라 운영에서는 App Store Connect API로 수집한 ID를 씁니다.
- 결과는 `posted_replies.jsonl` 에 한 줄씩 남습니다. 등록한 리뷰는 검수 화면에서 "등록 완료 표시"를 누릅니다.

## 매일 자동 실행

`.github/workflows/refresh-reviews.yml` 이 1~5단계를 돌리고 `data/reviews.json` 을 커밋합니다. 저장소 Secrets 에 `ANTHROPIC_API_KEY` 를 넣고 Actions 탭에서 수동 실행합니다. 매일 09:00(KST) 실행은 `schedule` 주석을 풀면 됩니다. 검증에서 오류가 나면 데이터를 바꾸지 않고 멈춥니다.

이 워크플로는 기존 `data/reviews.json` 에 **누적**합니다. 이미 있는 리뷰는 ai 를 그대로 두고 원문·기존 답변·스토어 답변 여부만 최신으로 바꾸며, 새 id 만 분류·초안을 만들어 추가합니다. 작성자가 리뷰를 고쳐 내용이 바뀌면 그 리뷰만 다시 분석하고 `content_changed_at` 을 붙이며, 화면은 그 전에 저장된 검수 상태를 버리고 "내용 변경됨"으로 표시합니다. 수집일 기준 180일(`KEEP_DAYS`)보다 오래된 리뷰는 잘라냅니다. 여러 사람이 검수 상태를 공유하려면 아래처럼 DB로 옮깁니다.

## 운영 전환 때 바꿀 곳

| 지금(PoC) | 운영 | 코드 위치 |
|---|---|---|
| 공개 데이터 수집(scraper, RSS) | Play Developer API `reviews.list`, App Store Connect `customerReviews`. 출력 스키마는 그대로 | `fetch_reviews.py` |
| `data/reviews.json` 한 파일에 누적(정적 호스팅) | DB(예: Supabase, Firestore)에 리뷰·초안 저장, 화면은 API로 읽기 | 워크플로 5단계, `merge_for_site.py --base` |
| 검수 상태를 브라우저 localStorage 에 저장 | DB 테이블(review_status)에 저장, 담당자 로그인 | `assets/app.js` 의 `saveLocal()`("운영 전환:" 주석) |
| 승인 답변을 파일로 내려받아 수동 실행 | 승인 즉시 큐에 넣고 등록 스크립트가 처리, 결과를 상태에 반영 | `post_replies.py` |
| 알림 없음 | P1 신규 건 메신저 알림 | 워크플로 "운영 전환:" 주석 |

사람이 승인하기 전에는 자동 등록하지 않습니다. 운영이 안정되면 P3 템플릿 답변부터 자동 등록으로 넓힐 수 있습니다.
