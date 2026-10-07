# 원시인 형님들 키우기 리뷰 검수함

썬더게임즈(T.G Inc) 「원시인 형님들 키우기 : 문명 x 방치형 RPG」의 Google Play·App Store 리뷰를 유형별로 나누고, 한국어로 옮기고, GM Mr. Bow 말투의 답변 초안을 만들어 담당자가 **검수만 하면 되게** 만든 PoC입니다.

> 썬더게임즈 AI 전환 컨설팅 PoC — 공개 리뷰 데이터 기반, 실제 스토어에 답변을 등록하지 않음

- 사이트: https://namojo.github.io/game-review/
- 데이터: 2026-08-08 ~ 2026-10-06 공개 리뷰 378건(Google Play 362, App Store 16, 13개 언어)

## 왜 만들었나

운영 담당자는 리뷰 1건에 약 2분을 씁니다. 다른 업무에 밀리면 며칠 치를 몰아서 처리하고, 별점별 고정 문구 4~5종을 돌려 씁니다(이번 데이터에서 스토어 답변 343건이 서로 다른 문구 50종, 13개 언어). 광고 불만과 결제 오류에 같은 사과문이 나갑니다. 이 도구는 수집부터 초안까지 자동으로 하고, 사람은 우선순위 높은 리뷰부터 읽고 고치고 승인합니다.

## 화면

| 탭 | 내용 |
|---|---|
| 대시보드 | 핵심 지표(예상 절감 시간과 계산식 포함), 일별 리뷰 수와 7일 평균 별점, 유형·언어 분포, 지금 쓰는 답변 문구 반복 현황, 부서별 전달 목록(회의용 복사), 현재 템플릿 vs AI 맞춤 답변 비교 |
| 검수 인박스 | P1 → P2 → P3(같은 등급 안에서 사람 검수 표시 먼저) 순서의 목록, 필터·검색, 원문·번역·분류 수정, 350자 카운터, 기존 답변 비교, 승인·보류·템플릿·복사·Claude로 다시 쓰기, P3 템플릿 일괄 승인(실행 취소), 승인 답변 JSON·CSV 내보내기, 새 리뷰 붙여넣기 |
| 운영 가이드 | 지금 방식과 자동화 뒤 흐름, 시간 비교, 운영 전환 체크리스트, 데이터 출처와 한계 |
| 설정 | Claude API 키(BYOK)·모델·답변 톤, 화면 테마, 검수 상태 백업·가져오기·초기화 |

키보드: `J`/`K` 이동, `A` 승인, `H` 보류, `C` 복사, `/` 검색. 주소(`#/inbox/{id}`)가 바뀌므로 새로고침해도 같은 리뷰가 열립니다.

## 로컬에서 열기

빌드가 없습니다. 정적 서버로 엽니다(`file://` 로 열면 브라우저가 데이터 파일 읽기를 막습니다).

```bash
python3 -m http.server 8765        # 이 폴더에서
# http://localhost:8765/
```

## 저장 위치와 보안

- 검수 상태(승인·보류·수정 답변·수동 분류)와 붙여넣은 리뷰는 **이 브라우저의 localStorage** 에만 저장됩니다. 서버가 없습니다. 설정 탭에서 JSON으로 백업·복원합니다.
- Claude API 키도 localStorage 에만 저장되고 요청은 브라우저에서 `api.anthropic.com` 으로 바로 갑니다. 브라우저 직접 호출에는 `anthropic-dangerous-direct-browser-access: true` 헤더가 필요하고, 같은 브라우저의 확장 프로그램이 키를 읽을 수 있습니다. 사용 한도를 건 시험용 키를 쓰고 다 쓰면 삭제하세요.
- 저장소에는 API 키·서비스 계정 키를 넣지 않습니다. 파이프라인은 환경 변수와 GitHub Actions Secrets 를 씁니다.
- 작성자 이름은 수집 단계에서 해시 별칭(`user-xxxxxx`)으로 바꿨고 화면에 보여주지 않습니다.

## 구조

```
index.html                 빌드 없이 열리는 SPA (해시 라우팅)
assets/app.css             색·글꼴·레이아웃 (라이트·다크)
assets/app.js              화면·상태·키보드·내보내기
assets/charts.js           SVG 차트(라이브러리 없음)
assets/claude.js           BYOK Claude 호출(다시 쓰기, 새 리뷰 분석)
assets/icon.png            앱 아이콘(Google Play 공개 이미지 축소본)
data/reviews.json          파이프라인 산출물(필드 계약: 아래)
pipeline/                  수집·초안·검증·병합·등록 스크립트 → pipeline/README.md
.github/workflows/refresh-reviews.yml   수동 실행 + (주석) 매일 09:00 KST
```

`data/reviews.json` 은 `meta`, `stats`, `templates`(언어별 짧은 칭찬 템플릿), `reviews[]` 로 이루어집니다. 리뷰마다 원본 필드와 `ai`(category, sentiment, priority, route_to, needs_cs, summary_ko, detected_lang, translation_ko, reply, reply_ko, reply_strategy, confidence, flags)가 있고, `ai` 가 `null` 이면 화면에 "분석 대기"로 보입니다. 필드 이름을 바꾸면 `pipeline/merge_for_site.py` 와 `assets/app.js` 를 함께 고칩니다.

## 데이터 출처와 한계

- Google Play: google-play-scraper(13개 언어별 최신순). App Store: iTunes 고객 리뷰 RSS(국가별 최근 리뷰만, 개발자 답변 없음).
- 평균 별점은 수집한 리뷰의 평균이고 스토어 누적 평점(Play 4.47)과 다릅니다.
- 예상 절감 시간 = 리뷰 수 × (2분 − 검수 0.5분). 2분은 운영팀 실측, 0.5분은 가정값입니다.
- 답변 초안은 AI가 만들었으며 사람이 검수하기 전에는 게시하지 않습니다.

## 운영 전환

`pipeline/README.md` 의 "운영 전환 때 바꿀 곳"을 보세요. 요약하면 수집을 공식 API로, 검수 상태를 DB로, 승인 답변 등록을 자동 큐로 옮기고 매일 스케줄을 켭니다.
