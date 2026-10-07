#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터로 시연] 리포트 숫자 검증 — 리포트의 숫자가 findings.json 의 '그 항목'에서 '같은 부호'로 왔는지 확인한다.

  python3 check_report_numbers.py sample_daily_report.md [--findings findings.json]

검사하는 것
  1. 출처: 모든 숫자는 findings.json 의 값(그대로·반올림·×100 퍼센트)이거나, reason 같은 문장에 이미 적힌 숫자여야 한다.
  2. 단위: 리포트에서 %가 붙은 숫자는 퍼센트 값과만, $가 붙거나 단위가 없는 숫자는 퍼센트가 아닌 값과만 맞춘다.
  3. 부호: 리포트에 +/−가 붙은 숫자는 부호가 있는 변화량(change·vs_prev·drivers 등, 또는 문장에 부호가 적힌 숫자)과
     같은 부호일 때만 통과한다. "하락·감소"처럼 방향을 말로 쓴 경우도 부호로 본다.
  4. 항목 대응: 줄에 발견 ID(F01)·제안 ID(B01)·캠페인·국가·소재 이름이 나오면, 그 뒤의 숫자는 그 키에 해당하는
     항목(finding·proposal·국가별 요약·near_miss)에서만 허용한다. ID 와 이름이 함께 나오면 둘 다 맞는 항목으로 좁힌다.
     표의 행은 첫 칸의 ID 가 행 전체의 주인이다. 키가 하나도 없는 줄은 summary·checks 같은 전체 값에서 찾는다.
  5. 제안 ID·캠페인 짝: 제안 ID(B01) 바로 뒤에 캠페인 이름을 쓰면 그 제안의 캠페인이어야 한다.

검사하지 못하는 것(사람 검수나 다른 장치가 필요)
  - 키가 없는 문장 안에서 같은 단위·같은 부호의 다른 전체 값으로 바꿔치기한 경우(예: 키 없이 쓴 두 개의 '건수'를 서로 바꿈)
  - 날짜(MM/DD, YYYY-MM-DD)와 줄 맨 앞 목록 번호 — 검사에서 뺀다
  - 숫자가 아닌 서술의 오류(원인 추측, 조치 문구 왜곡) — report_prompt.md 의 규칙과 담당자 검수가 맡는다

종료 코드 0 = 통과, 1 = 문제 있음.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 앞에 글자·밑줄·숫자가 붙은 숫자(MV03, T1, D7, 1200x628 의 628)는 이름의 일부로 보고 건너뛴다
NUM = re.compile(r"(?<![A-Za-z_0-9.])([-−+]?)(\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%)?(?![A-Za-z_])")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|(?<!\d)\d{1,2}/\d{1,2}(?!\d)")
LIST_NO = re.compile(r"^\s*\d{1,2}[.)]\s")
DOWN_WORDS = ("하락", "감소", "줄", "떨어")
UP_WORDS = ("상승", "증가", "늘", "올랐", "급등")
SIGNED_KEYS = ("change", "vs_prev", "drivers", "projected_vs_budget", "budget_net_change", "headroom")
ID_RE = re.compile(r"(?<![A-Za-z0-9])([FB]\d{2})(?![0-9])")


def parse_numbers(text):
    """(값, 소수 자릿수, 단위 'pct'|'num', 부호 +1|-1|0, 시작 위치, 원문) 목록."""
    out = []
    for m in NUM.finditer(text):
        sign_ch, dollar, whole, frac, percent = m.groups()
        value = float(whole.replace(",", "") + (frac or ""))
        sign = -1 if sign_ch in ("-", "−") else 1 if sign_ch == "+" else 0
        out.append((value, len(frac) - 1 if frac else 0, "pct" if percent else "num", sign, m.start(), m.group(0)))
    return out


class Entity:
    def __init__(self, name, keys):
        self.name = name
        self.keys = {k for k in keys if k}
        self.values = []                     # (절댓값, 단위, 부호 있는 값인가, 부호)

    def add_number(self, x, signed):
        sign = (1 if x > 0 else -1 if x < 0 else 0) if signed else 0
        self.values.append((abs(x), "num", signed, sign))
        self.values.append((abs(x) * 100, "pct", signed, sign))

    def add_text(self, s):
        for v, _, unit, sign, _, _ in parse_numbers(DATE.sub(" ", s)):
            self.values.append((v, unit, sign != 0, sign))
        for part in re.findall(r"\d+", s):   # 날짜 조각
            self.values.append((float(part), "num", False, 0))

    def walk(self, x, key=""):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            self.add_number(x, any(k in key for k in SIGNED_KEYS))
        elif isinstance(x, str):
            self.add_text(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                self.walk(v, f"{key}.{k}" if key else k)
        elif isinstance(x, list):
            for v in x:
                self.walk(v, key)

    def allows(self, value, decimals, unit, sign):
        for a, u, signed, s in self.values:
            if u != unit or round(a, decimals) != round(value, decimals):
                continue
            if sign == 0 or (signed and s == sign):
                return True
        return False


def short_code(name):
    return name.split("_")[0] if name and "_" in name else None


def build_entities(findings):
    ents = []
    for f in findings.get("findings", []):
        e = Entity(f["id"], [f["id"], f.get("campaign"), f.get("country"), f.get("creative"), short_code(f.get("creative"))])
        e.walk(f)
        ents.append(e)
    for p in findings.get("budget_proposals", []):
        e = Entity(p["id"], [p["id"], p.get("campaign")])
        e.walk(p)
        ents.append(e)
    for c, v in findings.get("summary", {}).get("by_country", {}).items():
        e = Entity(f"by_country.{c}", [c])
        e.walk(v)
        ents.append(e)
    for i, n in enumerate(findings.get("near_misses", [])):
        e = Entity(f"near_miss.{i}", [n.get("campaign"), n.get("country"), n.get("creative"), short_code(n.get("creative"))])
        e.walk(n)
        ents.append(e)
    glob = Entity("global", [])
    rest = {k: v for k, v in findings.items() if k not in ("findings", "budget_proposals", "near_misses")}
    glob.walk(rest)
    return ents, glob


def key_positions(line, all_keys):
    """줄 안에서 키가 나온 위치. 긴 이름부터 찾고 겹치는 짧은 키는 버린다."""
    taken, found = [], []
    for k in sorted(all_keys, key=len, reverse=True):
        pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(k) + r"(?![A-Za-z0-9])")
        for m in pat.finditer(line):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), k))
    return sorted(found)


def word_sign(line, end):
    tail = line[end:end + 8]
    if any(w in tail for w in DOWN_WORDS):
        return -1
    if any(w in tail for w in UP_WORDS):
        return 1
    return 0


def check(report, findings):
    ents, glob = build_entities(findings)
    all_keys = set().union(*(e.keys for e in ents))
    campaigns = {f.get("campaign") for f in findings.get("findings", [])} | \
                {x.get("campaign") for x in findings.get("budget_proposals", [])}
    campaigns.discard(None)
    problems = []
    for line in report.splitlines():
        clean = DATE.sub(lambda m: " " * len(m.group(0)), line)
        clean = LIST_NO.sub(lambda m: " " * len(m.group(0)), clean)
        keys = key_positions(clean, all_keys)
        if clean.lstrip().startswith("|"):
            # 표의 행: 첫 칸의 ID 가 행 전체의 주인이다. 다른 칸에서 참조로 언급한 ID(예: "추적 누락(F01·F02)")로 바꾸지 않는다
            first_end = clean.index("|", clean.index("|") + 1) if clean.count("|") > 1 else len(clean)
            keys = [(p, k) for p, k in keys if p < first_end or not ID_RE.fullmatch(k)]
        line_ids = {k for _, k in keys if ID_RE.fullmatch(k)}
        id_ents = [e for e in ents if e.keys & line_ids]
        # 제안 ID 바로 뒤에 캠페인 이름이 오면 그 제안의 캠페인이어야 한다(예: "B01(META_A+APP_AOS_T3 …)")
        for (p1, k1), (p2, k2) in zip(keys, keys[1:]):
            if k1.startswith("B") and ID_RE.fullmatch(k1) and k2 in campaigns:   # 제안은 캠페인이 하나로 정해져 있다
                owner = next(e for e in ents if e.name == k1)
                if k2 not in owner.keys:
                    problems.append(f"{k1}·{k2} — {k1} 의 캠페인이 아님")
        for value, decimals, unit, sign, pos, raw in parse_numbers(clean):
            if sign == 0:
                sign = word_sign(clean, pos + len(raw))
            before = [k for p, k in keys if p < pos]
            ctx = before[-1] if before else None
            if ctx is None:
                cands = id_ents or [glob]
            else:
                key_ents = [e for e in ents if ctx in e.keys]
                both = [e for e in key_ents if e in id_ents]
                cands = both or (id_ents if ID_RE.fullmatch(ctx) is None and id_ents else key_ents) or [glob]
            if any(e.allows(value, decimals, unit, sign) for e in cands):
                continue
            where = ",".join(e.name for e in cands)
            if any(e.allows(value, decimals, unit, 0) for e in cands):
                problems.append(f"{raw} — 부호가 [{where}] 와 다름")
            elif any(e.allows(value, decimals, unit, sign) for e in ents + [glob]):
                problems.append(f"{raw} — 다른 항목의 값([{where}] 에는 없음)")
            else:
                problems.append(f"{raw} — findings.json 에 없는 숫자")
    return problems


def count_numbers(report):
    n = 0
    for line in report.splitlines():
        clean = LIST_NO.sub(" ", DATE.sub(" ", line))
        n += len(parse_numbers(clean))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--findings", default=str(HERE / "findings.json"))
    args = ap.parse_args()
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    report = Path(args.report).read_text(encoding="utf-8")
    problems = check(report, findings)
    total = count_numbers(report)
    if problems:
        print(f"실패: 숫자 {total}개 중 {len(problems)}개 문제")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"통과: 리포트의 숫자 {total}개가 모두 findings.json 의 해당 항목에서 같은 부호로 왔다")


if __name__ == "__main__":
    main()
