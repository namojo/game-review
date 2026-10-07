#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터로 시연] 숫자 검증기 시험 — 원본 리포트는 통과하고, 일부러 틀리게 고친 리포트는 모두 막혀야 한다.

  python3 test_check_report_numbers.py        # 표준 라이브러리 unittest

변형 1~5 는 QA 트랙 D 가 쓴 바꿔치기 시험이다(부호 반전 2건, 다른 항목 값으로 바꿔치기 3건).
"""
import json
import unittest
from pathlib import Path

from check_report_numbers import check

HERE = Path(__file__).resolve().parent
REPORT = (HERE / "sample_daily_report.md").read_text(encoding="utf-8")
FINDINGS = json.loads((HERE / "findings.json").read_text(encoding="utf-8"))


def mutate(old, new, count=1):
    assert REPORT.count(old) >= 1, f"원본에 없는 문자열: {old}"
    return REPORT.replace(old, new, count)


def swap(a, b):
    assert a in REPORT and b in REPORT
    return REPORT.replace(a, "\0").replace(b, a).replace("\0", b)


CASES = {
    # QA 트랙 D 의 변형 5건
    "1 부호 반전: 지출 −1% → +1%": mutate("직전 7일 평균 대비 −1%", "직전 7일 평균 대비 +1%"),
    "2 부호 반전: MV03 CTR −35% → +35%": mutate("MV03_tower_climb_15s(BR, CTR −35%", "MV03_tower_climb_15s(BR, CTR +35%"),
    "3 바꿔치기: KR 지출에 US 값": mutate("지출 KR $233·US $349", "지출 KR $349·US $349"),
    "4 바꿔치기: F04 CPI 에 ID 전체 CPI": mutate("CPI $0.92 — 직전", "CPI $0.64 — 직전"),
    "5 바꿔치기: B01·B02 제안액 맞바꿈(표)": swap("$250 → $200 (−20%)", "$350 → $405 (+16%)"),
    # 추가 변형
    "6 부호 반전: 표의 F05 CTR −35% → +35%": mutate("1.45% 대비 −35%", "1.45% 대비 +35%"),
    "7 바꿔치기: 오늘 할 일의 B01·B02 금액만": swap("$250→$200", "$350→$405"),
    "7b ID·캠페인 짝 오류: B01 옆에 B02 의 캠페인": swap("META_A+APP_AOS_T3 $250→$200", "META_A+APP_AOS_T1 $350→$405"),
    "8 바꿔치기: 국가 표 KR·US 지출": swap("| $524.51 |", "| $400.84 |"),
    "9 말로 쓴 방향 반전: −35% → 35% 상승": mutate("MV03_tower_climb_15s(BR, CTR −35%", "MV03_tower_climb_15s(BR, CTR 35% 상승"),
    "10 지어낸 숫자: CPI $1.37": mutate("CPI $1.28(+14%)", "CPI $1.37(+14%)"),
    "11 지어낸 합계: 두 국가 지출 합 $582": mutate("지출 KR $233·US $349", "지출 $582"),
    "12 지어낸 숫자: 월말 예상 $27,900": mutate("월말 예상 $27,613", "월말 예상 $27,900"),
}


class CheckReportNumbersTest(unittest.TestCase):
    def test_original_passes(self):
        self.assertEqual(check(REPORT, FINDINGS), [])

    def test_mutations_blocked(self):
        for name, text in CASES.items():
            with self.subTest(name):
                self.assertNotEqual(text, REPORT, "변형이 적용되지 않음")
                self.assertTrue(check(text, FINDINGS), f"막지 못함: {name}")


if __name__ == "__main__":
    for name, text in CASES.items():
        problems = check(text, FINDINGS)
        print(f"{'차단' if problems else '통과(실패!)'} | {name} | {problems[:2]}")
    unittest.main(verbosity=1)
