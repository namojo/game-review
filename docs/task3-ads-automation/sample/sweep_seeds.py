#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터로 시연] 시드 반복 검증 — 잡음만 바꿔 N번 돌리고 탐지율·오탐을 센다.

  python3 sweep_seeds.py [N=30]

임시 폴더에서 실행하므로 sample/ 의 결과 파일을 건드리지 않는다. 표준 라이브러리만 쓴다.

세는 기준
  - 탐지율: 심어 둔 문제(아래 EXPECTED)가 findings 에 (유형·캠페인·국가·소재)로 나온 시드 수 / N
  - 오탐: EXPECTED 에 없는 findings 항목 수. 시드마다 고유한 (유형·캠페인·국가·소재) 묶음을 1건으로 세고 N개 시드를 합한다.
          예산 제안(budget_proposals)은 오탐 수에 넣지 않고 따로 센다.
  - 미끼(META_A+APP_AOS_T3 · ID · MV02)가 findings 에 나오면 오탐으로도 센다.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = {
    ("data_issue", "META_A+APP_IOS_T1", "KR", None),
    ("data_issue", "META_A+APP_IOS_T1", "US", None),
    ("pacing", None, None, None),
    ("anomaly", "GOOG_ACi_AOS_T3", "ID", None),
    ("creative_fatigue", "META_A+APP_AOS_T2", "BR", "MV03_tower_climb_15s"),
    ("creative_fatigue", "GOOG_ACi_AOS_T1", None, "GV02_gear_merge_15s"),
    ("scale_creative", "META_A+APP_AOS_T1", None, "MP01_totem_tap_playable"),
}
EXPECTED_BUDGET = {("budget_up", "META_A+APP_AOS_T1"), ("budget_down", "META_A+APP_AOS_T3")}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    src = (HERE / "generate_sample_data.py").read_text(encoding="utf-8")
    hit, extra, bhit, bextra = Counter(), Counter(), Counter(), Counter()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(HERE / "analyze_ads.py", tmp)
        for seed in range(1, n + 1):
            (tmp / "generate_sample_data.py").write_text(re.sub(r"SEED = \d+", f"SEED = {seed}", src), encoding="utf-8")
            subprocess.run([sys.executable, "generate_sample_data.py"], cwd=tmp, check=True, capture_output=True)
            subprocess.run([sys.executable, "analyze_ads.py"], cwd=tmp, check=True, capture_output=True)
            d = json.loads((tmp / "findings.json").read_text(encoding="utf-8"))
            got = {(f["type"], f["campaign"], f["country"], f["creative"]) for f in d["findings"]}
            for e in EXPECTED & got:
                hit[e] += 1
            for g in got - EXPECTED:
                extra[g] += 1
            gb = {(p["type"], p["campaign"]) for p in d["budget_proposals"] if p["type"] != "hold"}
            for e in EXPECTED_BUDGET & gb:
                bhit[e] += 1
            for g in gb - EXPECTED_BUDGET:
                bextra[g] += 1

    print(f"[가상 데이터] 시드 1~{n}")
    print("탐지율")
    for e in sorted(EXPECTED, key=str):
        print(f"  {hit[e]:>3}/{n}  {' · '.join(x for x in e if x)}")
    for e in sorted(EXPECTED_BUDGET):
        print(f"  {bhit[e]:>3}/{n}  {' · '.join(e)}")
    total = sum(extra.values())
    print(f"오탐(findings 기준) {total}건 / {n}일 = 하루 평균 {total / n:.2f}건")
    for g, c in extra.most_common():
        print(f"  {c:>3}  {' · '.join(x for x in g if x)}")
    print(f"예산 제안 중 기대 밖 {sum(bextra.values())}건")


if __name__ == "__main__":
    main()
