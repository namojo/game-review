#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터로 시연] findings.json → Claude API → 일일 리포트(Markdown) → 숫자 검증

  python3 make_report.py --dry-run        # API 를 부르지 않고 보낼 요청만 출력(키 없이 확인)
  python3 make_report.py                  # 실제 호출. pip install anthropic, 인증 필요(ANTHROPIC_API_KEY 등)

숫자는 analyze_ads.py 가 계산한다. Claude 는 findings.json 만 받아 문장으로 바꾼다.
생성된 리포트는 check_report_numbers.py 로 검사하고, 입력에 없는 숫자가 하나라도 있으면 저장만 하고
발송 대상에서 뺀다(종료 코드 2).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from check_report_numbers import check

HERE = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"


def load_prompt(path):
    text = Path(path).read_text(encoding="utf-8")
    system = re.search(r"<!-- SYSTEM -->\n(.*?)<!-- /SYSTEM -->", text, re.S).group(1).strip()
    user = re.search(r"<!-- USER -->\n(.*?)<!-- /USER -->", text, re.S).group(1).strip()
    return system, user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", default=str(HERE / "findings.json"))
    ap.add_argument("--prompt", default=str(HERE / "report_prompt.md"))
    ap.add_argument("--out", default=str(HERE / "daily_report_generated.md"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    findings_text = Path(args.findings).read_text(encoding="utf-8")
    system, user_tpl = load_prompt(args.prompt)
    user = user_tpl.replace("{{FINDINGS_JSON}}", findings_text)
    request = dict(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},       # 짧은 요약 작업 — 낮은 effort 로 충분
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",                    # 드문 거절 시 서버가 권장 모델로 다시 실행
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if args.dry_run:
        preview = dict(request, messages=[{"role": "user", "content": user[:400] + " …(findings.json 생략)"}])
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    import anthropic                            # 실제 호출할 때만 필요

    client = anthropic.Anthropic()              # ANTHROPIC_API_KEY 또는 ant auth login 프로필
    try:
        response = client.beta.messages.create(**request)
    except anthropic.RateLimitError:
        sys.exit("요청 한도 초과 — 잠시 뒤 재실행(SDK 가 기본 2회 재시도함)")
    except anthropic.APIStatusError as e:
        sys.exit(f"API 오류 {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("네트워크 오류")

    if response.stop_reason == "refusal":
        sys.exit("모델이 요청을 거절함 — findings.json 을 사람이 직접 확인")
    # 서버 fallback 이 출력 도중에 일어나면 content 가 [잘린 text, fallback, 전체 text] 가 된다.
    # 마지막 fallback 블록 뒤의 text 만 리포트로 쓴다(앞 모델이 쓰다 만 부분을 이어 붙이지 않는다).
    blocks = list(response.content)
    last_fb = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    report = "".join(b.text for b in blocks[last_fb + 1:] if b.type == "text")
    if last_fb >= 0 or response.model != MODEL:
        print(f"[알림] 요청 모델 {MODEL} 대신 {response.model} 이(가) 리포트를 작성함(서버 fallback)", file=sys.stderr)
    Path(args.out).write_text(report, encoding="utf-8")

    problems = check(report, json.loads(findings_text))
    if problems:
        print(f"[발송 보류] 입력에 없는 숫자 {len(problems)}개: {problems[:10]}", file=sys.stderr)
        sys.exit(2)
    print(f"리포트 저장: {args.out} (숫자 검증 통과, 모델 {response.model})")


if __name__ == "__main__":
    main()
