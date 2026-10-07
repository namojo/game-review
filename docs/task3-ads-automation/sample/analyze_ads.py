#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터로 시연] UA 일일 성과 분석 — analysis-playbook 규칙 → findings.json

숫자 계산과 판정은 전부 이 스크립트가 한다. Claude 는 findings.json 만 받아 문장으로 바꾼다
(report_prompt.md). 그래서 리포트에 나오는 모든 숫자는 이 파일이 만든 값이어야 한다.

검사 순서(analysis-playbook.md)
  0 데이터 신선도·완결성  → data_issue      (추적 누락 의심은 분석보다 먼저 보고)
  1 예산 소진 속도        → pacing
  2 지표 이상(강건 z)     → anomaly
  3 소재 피로             → creative_fatigue
  4 우수 소재             → scale_creative
  5 예산 재배분           → budget_up / budget_down / hold

규칙의 기준값은 '초기값'이다. 4주 운영 후 자사 데이터로 조정한다(RULES 한곳에 모아 둠).

실행: python3 analyze_ads.py [--data ads_daily_sample.csv] [--config budget_config.json]
                             [--as-of 2026-10-06] [--out findings.json]
표준 라이브러리만 쓴다.
"""
import argparse
import calendar
import csv
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent

RULES = {
    "track_min_spend": 30.0,          # 이 지출 이상인데 설치 0이면 추적 누락 의심
    "track_ios_min_days": 2,          # iOS 는 SKAN 포스트백 지연이 있어 2일 연속일 때만 판정
    "pacing_tolerance": 0.15,         # 월 예산 소진 비율 / 경과일 비율 이 ±15% 밖
    "anomaly_baseline_days": 14,
    "anomaly_z": 3.0,
    "anomaly_min_installs": 20,       # 어제 표본이 이보다 작으면 이상 탐지 생략
    "anomaly_min_spend": 50.0,
    "anomaly_min_change": {"cpi": 0.25, "ctr": 0.20, "roas_d0": 0.25},  # 추가 안전장치: 실무상 의미 있는 크기만
    "fatigue_window": 3,
    "fatigue_window_wide": 7,         # 추가 안전장치: 3일 창의 사건 수가 적으면 7일 창으로 넓힌다
    "fatigue_min_events": 100,        # 창마다 분자(클릭 또는 설치) 100건 미만이면 넓힌다
    "fatigue_drop": -0.25,
    "fatigue_meta_frequency": 3.0,
    "creative_min_impressions": 10000,  # kpi-framework: 소재 판정 최소 표본
    "creative_min_installs": 30,
    "significance_z": 1.96,           # 추가 안전장치: 두 비율 차이 검정(95%)
    "scale_top_share": 0.20,
    "scale_min_lift": 1.30,           # 캠페인 중앙값 대비 IPM 1.3배 이상
    "budget_min_spend_7d": 200.0,
    "budget_up_ratio": 1.20,
    "budget_up_strong_ratio": 1.50,
    "budget_down_ratio": 0.70,
    "budget_down_days": 3,
    "budget_step_up": 0.15,
    "budget_step_up_strong": 0.20,
    "budget_step_down": -0.20,
    "budget_max_change": 0.20,        # 변경 폭 상한 — 학습 단계 재시작을 피하려고
    "budget_cooldown_days": 3,        # 같은 캠페인 쿨다운
    "cpi_stable_tolerance": 0.15,     # 최근 3일 CPI 가 직전 14일 중앙값 대비 +15% 이내
}

TYPE_ORDER = {"data_issue": 0, "pacing": 1, "anomaly": 2, "creative_fatigue": 3, "scale_creative": 4}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ADDITIVE = ["spend", "impressions", "clicks", "installs", "video_3s_views", "d1_retained", "d7_retained",
            "rev_iap_d0", "rev_iap_d7", "rev_ad_d0", "rev_ad_d7"]
NETWORK_KO = {"meta": "메타", "google": "구글"}


# ---------- 공통 도구 ----------
def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]      # 머리말(가상 데이터 표시) 건너뛰기
    rows = []
    for r in csv.DictReader(lines):
        row = dict(r)
        row["date"] = date.fromisoformat(r["date"])
        for k in ADDITIVE + ["frequency"]:
            v = r.get(k, "")
            row[k] = float(v) if v not in ("", None) else None
        rows.append(row)
    return rows


def agg(rows):
    s = defaultdict(float)
    for r in rows:
        for k in ADDITIVE:
            if r[k] is not None:
                s[k] += r[k]
    return s


def rev_d0(s):
    return s["rev_iap_d0"] + s["rev_ad_d0"]


def metrics(s):
    imp, clk, ins, sp = s["impressions"], s["clicks"], s["installs"], s["spend"]
    return {
        "spend": sp,
        "installs": ins,
        "cpi": sp / ins if ins else None,
        "ctr": clk / imp if imp else None,
        "cvr": ins / clk if clk else None,
        "ipm": ins / imp * 1000 if imp else None,
        "cpm": sp / imp * 1000 if imp else None,
        "roas_d0": rev_d0(s) / sp if sp else None,
    }


def robust_z(x, series, noise_rel=0.0):
    """중앙값·MAD 기반 z. noise_rel 은 표본 크기에서 오는 상대 잡음(1/√n) — 14개 점으로 잰 MAD 가
    우연히 작을 때 작은 변화가 이상으로 잡히는 것을 막는 하한이다."""
    med = median(series)
    mad = median([abs(v - med) for v in series])
    scale = max(1.4826 * mad, abs(med) * noise_rel, abs(med) * 0.01, 1e-9)
    return (x - med) / scale, med


def two_prop_z(x1, n1, x2, n2):
    """비율 x2/n2 가 x1/n1 과 다른지 — z 값(음수면 감소)."""
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) if 0 < p < 1 else 0
    return ((x2 / n2) - (x1 / n1)) / se if se else 0.0


def pct(x, digits=0):
    """+12% / −31% 형식(유니코드 마이너스)."""
    s = f"{x * 100:+.{digits}f}%"
    if float(s[1:-1]) == 0:
        return f"{0:.{digits}f}%"
    return s.replace("-", "−")


def usd(x, digits=0):
    return f"${x:,.{digits}f}"


def md(d):
    return d.strftime("%m/%d")


def r2(x, n=4):
    return None if x is None else round(x, n)


# ---------- 분석 ----------
class Analyzer:
    def __init__(self, rows, config, as_of):
        self.rows = [r for r in rows if r["date"] <= as_of]
        self.cfg = config
        self.as_of = as_of
        self.report_date = as_of + timedelta(days=1)
        self.camps = {c["campaign_name"]: c for c in config["campaigns"]}
        self.findings = []
        self.proposals = []
        self.near_misses = []
        self.checks = {}
        self.blocked = defaultdict(dict)    # campaign → {예산 판단을 막는 이유: [국가]} — 데이터 문제: 모든 제안 보류
        self.no_increase = {}               # campaign → [이유] — 지표 이상: 원인 확인 전 증액만 막는다
        self.by = defaultdict(list)         # (campaign, country, date) → rows
        for r in self.rows:
            self.by[(r["campaign_name"], r["country"], r["date"])].append(r)

    def day_rows(self, d, **filt):
        return [r for r in self.rows if r["date"] == d and all(r[k] == v for k, v in filt.items())]

    def range_rows(self, d0, d1, **filt):
        return [r for r in self.rows if d0 <= r["date"] <= d1 and all(r[k] == v for k, v in filt.items())]

    def scopes(self):
        return sorted({(r["campaign_name"], r["country"]) for r in self.rows})

    def block(self, camp, reason, country=None):
        countries = self.blocked[camp].setdefault(reason, [])
        if country and country not in countries:
            countries.append(country)

    def blockers(self, camp):
        return [f"{r}({'·'.join(c)})" if c else r for r, c in self.blocked.get(camp, {}).items()]

    def add(self, **f):
        f.setdefault("country", None)
        f.setdefault("creative", None)
        self.findings.append(f)

    # 0. 데이터 신선도·완결성
    def check_data(self):
        flagged = 0
        for net in sorted({c["network"] for c in self.cfg["campaigns"]}):
            if not self.day_rows(self.as_of, network=net):
                flagged += 1
                self.add(type="data_issue", severity="high", network=net, campaign=None, metric="freshness",
                         value=0, baseline=None, change=None,
                         reason=f"{NETWORK_KO[net]} {md(self.as_of)} 데이터 행 없음 — 수집 작업 실패 또는 지연",
                         action="수집 로그·토큰 만료 확인 후 재수집. 이 매체의 판단은 모두 보류",
                         confidence="high")
                for c in self.cfg["campaigns"]:
                    if c["network"] == net:
                        self.block(c["campaign_name"], "데이터 미수집")

        evaluated = 0
        for camp, country in self.scopes():
            today = agg(self.by[(camp, country, self.as_of)])
            if today["spend"] < RULES["track_min_spend"]:
                continue
            evaluated += 1
            if today["installs"] > 0:
                continue
            # 설치 0이 며칠째인지
            streak, spend_s, clicks_s = 0, 0.0, 0.0
            d = self.as_of
            while True:
                s = agg(self.by[(camp, country, d)])
                if s["spend"] > 0 and s["installs"] == 0:
                    streak += 1
                    spend_s += s["spend"]
                    clicks_s += s["clicks"]
                    d -= timedelta(days=1)
                else:
                    break
            info = self.camps.get(camp, {})
            platform = info.get("platform", "")
            if platform == "ios" and streak < RULES["track_ios_min_days"]:
                self.near_misses.append({"check": "data_issue", "campaign": camp, "country": country,
                                         "note": "iOS 설치 0 하루 — SKAN 포스트백 지연 가능, 내일 재확인"})
                continue
            start = self.as_of - timedelta(days=streak - 1)
            base_days = [agg(self.by[(camp, country, start - timedelta(days=i))])["installs"]
                         for i in range(1, 15)]
            base_inst = median(base_days)
            flagged += 1
            span = f"{md(start)}~{md(self.as_of)} {streak}일 연속" if streak > 1 else md(self.as_of)
            self.add(type="data_issue", severity="high" if streak >= 2 or spend_s >= 100 else "medium",
                     network=info.get("network"), campaign=camp, country=country, metric="installs",
                     value=0, baseline=base_inst, change=-1.0,
                     days=streak, spend_during=round(spend_s, 2), clicks_during=int(clicks_s),
                     reason=(f"{span} 지출 {usd(spend_s)}·클릭 {int(clicks_s):,}건인데 MMP 설치 0 "
                             f"(직전 14일 하루 중앙값 {base_inst:g}건)"),
                     action=("MMP에서 이 매체 연동·포스트백 수신 확인(iOS 는 SKAN·최신 빌드 SDK 이벤트 포함). "
                             "확인 전까지 이 캠페인 예산 판단 보류"),
                     confidence="high")
            self.block(camp, "추적 누락 의심", country)

        # 광고 수익 미적재(하이브리드 ROAS 가 반토막 나는 흔한 사고) — 매체 단위
        rev_checks = 0
        for net in sorted({c["network"] for c in self.cfg["campaigns"]}):
            today = agg(self.day_rows(self.as_of, network=net))
            if today["installs"] < 100:
                continue
            rev_checks += 1
            hist = agg(self.range_rows(self.as_of - timedelta(days=14), self.as_of - timedelta(days=1),
                                       network=net))
            share = hist["rev_ad_d0"] / rev_d0(hist) if rev_d0(hist) else 0
            if today["rev_ad_d0"] == 0 and share > 0.10:
                flagged += 1
                self.add(type="data_issue", severity="medium", network=net, campaign=None, metric="rev_ad_d0",
                         value=0, baseline=round(share, 3), change=-1.0,
                         reason=f"{NETWORK_KO[net]} 설치 코호트의 {md(self.as_of)} 광고 수익 0 (직전 14일 광고 수익 비중 {pct(share)})",
                         action="미디에이션 노출 단위 수익 적재 확인. D0 ROAS 판단 보류",
                         confidence="high")
        self.checks["data_issue"] = {"networks": len({c["network"] for c in self.cfg["campaigns"]}),
                                     "scopes_evaluated": evaluated, "revenue_checks": rev_checks,
                                     "flagged": flagged}

    # 1. 예산 소진 속도
    def check_pacing(self):
        y, m = self.as_of.year, self.as_of.month
        days_in_month = calendar.monthrange(y, m)[1]
        elapsed = self.as_of.day / days_in_month
        remaining = days_in_month - self.as_of.day
        month_start = date(y, m, 1)
        self.pacing = {}
        flagged = 0
        for net, monthly in self.cfg["monthly_budget"].items():
            mtd = agg(self.range_rows(month_start, self.as_of, network=net))["spend"]
            daily_now = sum(c["daily_budget"] for c in self.cfg["campaigns"] if c["network"] == net)
            projected = mtd + daily_now * remaining
            ratio = (mtd / monthly) / elapsed
            needed = (monthly - mtd) / remaining if remaining else 0
            self.pacing[net] = {"monthly_budget": monthly, "mtd_spend": round(mtd, 2),
                                "mtd_share": round(mtd / monthly, 4), "elapsed_share": round(elapsed, 4),
                                "pace_ratio": round(ratio, 3), "daily_budget_now": daily_now,
                                "projected_month_spend": round(projected, 2),
                                "projected_vs_budget": round(projected / monthly - 1, 4),
                                "daily_needed_to_fit": round(needed, 2),
                                "headroom_daily": round(needed - daily_now, 2)}
            if abs(ratio - 1) <= RULES["pacing_tolerance"]:
                continue
            flagged += 1
            over = ratio > 1
            changes = [c for c in self.cfg["campaigns"] if c["network"] == net
                       and c["last_budget_change"]["from"] is not None
                       and c["last_budget_change"]["date"] >= month_start.isoformat()]
            cause = ""
            if changes:
                c = changes[0]
                ch = c["last_budget_change"]
                cause = (f" 이번 달 일 예산 변경: {c['campaign_name']} {md(date.fromisoformat(ch['date']))} "
                         f"{usd(ch['from'])}→{usd(ch['to'])}.")
            proj_txt = f"{usd(projected)}({pct(projected / monthly - 1)})"
            self.add(type="pacing", severity="high" if abs(ratio - 1) > 0.30 else "medium",
                     network=net, campaign=None, metric="mtd_spend_share",
                     value=round(mtd / monthly, 4), baseline=round(elapsed, 4), change=round(ratio - 1, 3),
                     reason=(f"{NETWORK_KO[net]} {m}월 예산 {usd(monthly)} 중 {usd(mtd)}({pct(mtd / monthly, 1).lstrip('+')}) 소진 — "
                             f"경과일 비율 {pct(elapsed, 1).lstrip('+')}. 현재 일 예산 합계 {usd(daily_now)} 유지 시 "
                             f"월말 예상 {proj_txt}.{cause}"),
                     action=(f"월 배정 상향 승인 또는 남은 기간 일 예산 합계를 {usd(needed)} 이하로 조정"
                             if over else f"집행 막힘 확인(심사 거절·입찰 상한). 남은 기간 일 {usd(needed)} 까지 집행 가능"),
                     confidence="high")
        self.checks["pacing"] = {"networks": len(self.cfg["monthly_budget"]), "flagged": flagged}

    # 2. 지표 이상
    def daily_metric(self, camp, country, d):
        return metrics(agg(self.by[(camp, country, d)]))

    def check_anomaly(self):
        bad_dir = {"cpi": 1, "ctr": -1, "roas_d0": -1}
        evaluated, flagged = 0, 0
        blocked_scopes = {(f["campaign"], f["country"]) for f in self.findings if f["type"] == "data_issue"}
        for camp, country in self.scopes():
            if (camp, country) in blocked_scopes:
                continue
            today = self.daily_metric(camp, country, self.as_of)
            if today["installs"] < RULES["anomaly_min_installs"] or today["spend"] < RULES["anomaly_min_spend"]:
                continue
            evaluated += 1
            base_days = [self.as_of - timedelta(days=i) for i in range(1, RULES["anomaly_baseline_days"] + 1)]
            base = [self.daily_metric(camp, country, d) for d in base_days]
            s_today = agg(self.by[(camp, country, self.as_of)])
            noise = {"cpi": 1 / math.sqrt(s_today["installs"]), "ctr": 1 / math.sqrt(max(s_today["clicks"], 1)),
                     "roas_d0": 1.5 / math.sqrt(s_today["installs"])}     # 수익은 소수 결제자에 몰려 더 출렁인다
            res = {}
            for m, sign in bad_dir.items():
                series = [b[m] for b in base if b[m] is not None]
                if len(series) < 7 or today[m] is None:
                    continue
                z, med = robust_z(today[m], series, noise[m])
                res[m] = {"z": z, "value": today[m], "baseline": med, "change": today[m] / med - 1 if med else None}
            hits = [m for m in ("cpi", "roas_d0", "ctr") if m in res and res[m]["z"] * bad_dir[m] > RULES["anomaly_z"]
                    and (res[m]["change"] or 0) * bad_dir[m] >= RULES["anomaly_min_change"][m]]
            if not hits:
                continue
            flagged += 1
            primary = hits[0]
            p = res[primary]
            # 며칠째인가
            streak, d = 0, self.as_of
            while True:
                dm = self.daily_metric(camp, country, d)
                series = [b[primary] for b in base if b[primary] is not None]
                if dm[primary] is None:
                    break
                zz, med_ = robust_z(dm[primary], series, noise[primary])
                if zz * bad_dir[primary] > RULES["anomaly_z"] and (dm[primary] / med_ - 1) * bad_dir[primary] >= \
                        RULES["anomaly_min_change"][primary] and streak < 14:
                    streak += 1
                    d -= timedelta(days=1)
                else:
                    break
            # CPI = CPM ÷ IPM, IPM = CTR × CVR × 1000 — 무엇이 움직였나
            drivers = {}
            for m in ("cpm", "ctr", "cvr"):
                series = [b[m] for b in base if b[m] is not None]
                med = median(series)
                drivers[m] = round(today[m] / med - 1, 3) if med else None
            info = self.camps.get(camp, {})
            net = info.get("network")
            # 같은 국가의 다른 매체는 정상인가 — 매체 쪽 원인인지, 국가·스토어 쪽 원인인지 가르는 단서
            peers = []
            for c2, k2 in self.scopes():
                if k2 != country or c2 == camp or self.camps.get(c2, {}).get("network") == net:
                    continue
                peers.append(c2)
            peer_txt, peer_normal = "", None
            if peers:
                peer_states = []
                for c2 in peers:
                    tm = self.daily_metric(c2, country, self.as_of)
                    bs = [self.daily_metric(c2, country, self.as_of - timedelta(days=i))["cpi"] for i in range(1, 15)]
                    bs = [b for b in bs if b is not None]
                    if tm["cpi"] is None or len(bs) < 7:
                        continue
                    zz, med = robust_z(tm["cpi"], bs, 1 / math.sqrt(max(tm["installs"], 1)))
                    peer_states.append((c2, tm["cpi"] / med - 1, zz))
                if peer_states:
                    c2, ch, zz = peer_states[0]
                    peer_normal = abs(zz) <= RULES["anomaly_z"]
                    state = "정상" if peer_normal else "이상"
                    peer_txt = f" 같은 국가 {c2} CPI {pct(ch)}({state}) → {'매체 쪽 원인 가능성' if state == '정상' else '국가·스토어 쪽 원인 가능성'}."
            label = {"cpi": "CPI", "ctr": "CTR", "roas_d0": "D0 ROAS"}[primary]
            fmt = (lambda v: usd(v, 2)) if primary == "cpi" else (lambda v: f"{v * 100:.1f}%")
            span = f"{md(self.as_of - timedelta(days=streak - 1))}부터 {streak}일째" if streak > 1 else "어제 하루"
            related = {m: {"value": r2(res[m]["value"]), "baseline": r2(res[m]["baseline"]),
                           "change": r2(res[m]["change"], 3), "z": round(res[m]["z"], 1)}
                       for m in res if m != primary}
            big = abs(p["z"]) > 5 and (p["change"] or 0) * bad_dir[primary] >= 0.5
            self.add(type="anomaly", severity="high" if streak >= 2 or big else "medium",
                     network=net, campaign=camp, country=country, metric=primary,
                     value=r2(p["value"]), baseline=r2(p["baseline"]), change=r2(p["change"], 3),
                     z=round(p["z"], 1), days=streak, drivers=drivers, related=related,
                     reason=(f"{label} {fmt(p['value'])} — 직전 14일 중앙값 {fmt(p['baseline'])} 대비 {pct(p['change'])} "
                             f"(강건 z {p['z']:.1f}, {span}). 분해: CPM {pct(drivers['cpm'])}, CTR {pct(drivers['ctr'])}, "
                             f"CVR {pct(drivers['cvr'])}.{peer_txt}"),
                     action=anomaly_action(drivers, peer_normal, NETWORK_KO.get(net, net)),
                     confidence="high" if today["installs"] >= 2 * RULES["anomaly_min_installs"] else "medium")
            self.no_increase.setdefault(camp, []).append(f"{label} 이상({country})")
        self.checks["anomaly"] = {"scopes_evaluated": evaluated, "metrics": list(bad_dir), "flagged": flagged}

    # 3. 소재 피로
    def creative_series(self):
        """Meta 는 캠페인×국가×소재, Google 은 캠페인×소재(에셋 지표는 겹치고 국가 분할을 전제하지 않는다)."""
        series = defaultdict(lambda: defaultdict(list))
        for r in self.rows:
            country = r["country"] if r["network"] == "meta" else None
            series[(r["campaign_name"], country, r["creative_id"])][r["date"]].append(r)
        return series

    def check_fatigue(self):
        evaluated, flagged = 0, 0
        self.fatigued = set()
        series = self.creative_series()
        for (camp, country, cr), days in sorted(series.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
            active = sorted(d for d, rs in days.items() if agg(rs)["impressions"] > 0)
            if len(active) < 2 * RULES["fatigue_window"] or active[-1] != self.as_of:
                continue
            net = days[active[-1]][0]["network"]
            metric = "ctr" if net == "meta" else "ipm"
            num = "clicks" if metric == "ctr" else "installs"
            w = RULES["fatigue_window"]
            first = agg([r for d in active[:w] for r in days[d]])
            last = agg([r for d in active[-w:] for r in days[d]])
            if min(first[num], last[num]) < RULES["fatigue_min_events"] and \
                    len(active) >= 2 * RULES["fatigue_window_wide"]:
                w = RULES["fatigue_window_wide"]
                first = agg([r for d in active[:w] for r in days[d]])
                last = agg([r for d in active[-w:] for r in days[d]])
            if min(first["impressions"], last["impressions"]) < RULES["creative_min_impressions"] or \
                    min(first["installs"], last["installs"]) < RULES["creative_min_installs"]:
                continue
            evaluated += 1
            v0 = first[num] / first["impressions"]
            v1 = last[num] / last["impressions"]
            change = v1 / v0 - 1
            if change > RULES["fatigue_drop"]:
                continue
            z = two_prop_z(first[num], first["impressions"], last[num], last["impressions"])
            # 같은 범위의 다른 소재도 같은 날들에 함께 떨어졌다면 소재 피로가 아니라 범위 단위 문제다(이상 탐지가 맡는다)
            others = [kv for kv in series.items() if kv[0][0] == camp and kv[0][1] == country and kv[0][2] != cr]
            o_first = agg([r for _, dd in others for d in active[:w] for r in dd.get(d, [])])
            o_last = agg([r for _, dd in others for d in active[-w:] for r in dd.get(d, [])])
            peer_change = None
            if o_first["impressions"] and o_last["impressions"] and o_first[num]:
                peer_change = (o_last[num] / o_last["impressions"]) / (o_first[num] / o_first["impressions"]) - 1
            relative = (1 + change) / (1 + peer_change) - 1 if peer_change is not None else change
            freq = None
            if net == "meta":
                freq = max(r["frequency"] or 0 for r in days[self.as_of])      # 비가산 지표: 합치지 않고 최신값
            name = days[self.as_of][0]["creative_name"]
            significant = z < -RULES["significance_z"]
            freq_ok = net != "meta" or (freq is not None and freq > RULES["fatigue_meta_frequency"])
            own_drop = relative <= RULES["fatigue_drop"] / 2
            if not (significant and freq_ok and own_drop):
                why = []
                if not own_drop:
                    why.append(f"같은 범위 다른 소재도 {pct(peer_change)} — 범위 단위 하락")
                if not freq_ok:
                    why.append(f"frequency {freq:.1f} ≤ {RULES['fatigue_meta_frequency']:.0f}")
                if not significant:
                    why.append(f"차이 검정 z {z:.1f}")
                self.near_misses.append({"check": "creative_fatigue", "network": net, "campaign": camp,
                                         "country": country, "creative": name, "metric": metric,
                                         "change": round(change, 3), "frequency": freq,
                                         "note": "하락은 있으나 규칙 미충족: " + ", ".join(why)})
                continue
            flagged += 1
            self.fatigued.add((camp, cr))
            unit = (lambda v: f"{v * 100:.2f}%") if metric == "ctr" else (lambda v: f"{v * 1000:.2f}")
            label = "CTR" if metric == "ctr" else "IPM"
            hook_txt = ""
            if first["video_3s_views"] and last["video_3s_views"]:
                h0 = first["video_3s_views"] / first["impressions"]
                h1 = last["video_3s_views"] / last["impressions"]
                hook_txt = f", 3초 조회율 {h0 * 100:.0f}%→{h1 * 100:.0f}%"
            freq_txt = f", frequency {freq:.1f}" if freq else ", 구글은 frequency 미제공 — IPM 기준만 적용"
            peer_txt = f", 같은 범위 다른 소재 {pct(peer_change)}" if peer_change is not None else ""
            sev = "high" if (net == "meta" and change <= -0.30) else "medium"
            self.add(type="creative_fatigue", severity=sev, network=net, campaign=camp, country=country,
                     creative=name, metric=metric, value=round(v1 * (1 if metric == "ctr" else 1000), 4),
                     baseline=round(v0 * (1 if metric == "ctr" else 1000), 4), change=round(change, 3),
                     frequency=freq, peer_change=r2(peer_change, 3), z=round(z, 1), window_days=w,
                     reason=(f"최근 {w}일 {label} {unit(v1)} — 첫 {w}일({md(active[0])}~) {unit(v0)} 대비 {pct(change)}"
                             f"{freq_txt}{hook_txt}{peer_txt}"),
                     action=("교체 소재 준비(같은 주제의 새 첫 3초 변형 2종), 이 소재 노출 비중 축소"
                             if net == "meta" else
                             "에셋 성과 라벨 확인 후 같은 광고 그룹에 새 영상 에셋 추가, 저성과 라벨이면 교체"),
                     confidence="high" if min(first["installs"], last["installs"]) >= 2 * RULES["creative_min_installs"] else "medium")
        self.checks["creative_fatigue"] = {"creatives_evaluated": evaluated, "flagged": flagged}

    # 4. 우수 소재
    def check_scale(self):
        d0 = self.as_of - timedelta(days=6)
        evaluated, flagged = 0, 0
        for camp in sorted({r["campaign_name"] for r in self.rows}):
            if camp in self.blocked:          # 추적 누락 범위의 IPM 은 믿을 수 없다
                continue
            per = defaultdict(list)
            for r in self.range_rows(d0, self.as_of, campaign_name=camp):
                per[r["creative_id"]].append(r)
            stats = {}
            for cr, rs in per.items():
                s = agg(rs)
                if s["impressions"] >= RULES["creative_min_impressions"] and s["installs"] >= RULES["creative_min_installs"]:
                    stats[cr] = (s, rs[0]["creative_name"], s["installs"] / s["impressions"] * 1000)
            if len(stats) < 3:
                continue
            evaluated += 1
            ipms = sorted(v[2] for v in stats.values())
            med = median(ipms)
            k = max(1, math.ceil(len(stats) * RULES["scale_top_share"]))
            top = sorted(stats.items(), key=lambda kv: -kv[1][2])[:k]
            for cr, (s, name, ipm) in top:
                if (camp, cr) in self.fatigued or ipm < med * RULES["scale_min_lift"]:
                    continue
                rest = agg([r for c2, rs in per.items() if c2 != cr for r in rs])
                z = two_prop_z(rest["installs"], rest["impressions"], s["installs"], s["impressions"])
                if z < RULES["significance_z"]:
                    continue
                flagged += 1
                share = s["spend"] / agg([r for rs in per.values() for r in rs])["spend"]
                info = self.camps.get(camp, {})
                self.add(type="scale_creative", severity="low", network=info.get("network"), campaign=camp,
                         creative=name, metric="ipm", value=round(ipm, 2), baseline=round(med, 2),
                         change=round(ipm / med - 1, 3), spend_share_7d=round(share, 3),
                         reason=(f"최근 7일 IPM {ipm:.2f} — 캠페인 소재 중앙값 {med:.2f}의 {ipm / med:.1f}배, "
                                 f"설치 {int(s['installs']):,}건, 지출 비중 {share * 100:.0f}%"),
                         action="같은 콘셉트 변형 2~3종 제작, 다른 Tier 캠페인에 테스트 투입(기존 소재는 끄지 않음)",
                         confidence="high" if s["installs"] >= 100 else "medium")
        self.checks["scale_creative"] = {"campaigns_evaluated": evaluated, "flagged": flagged}

    # 5. 예산 재배분
    def check_budget(self):
        mult_fb = self.cfg["d7_d0_multiplier_fallback"]
        targets = self.cfg["target_roas_d7"]
        d7_from, d7_to = self.as_of - timedelta(days=13), self.as_of - timedelta(days=7)   # D7 확정 코호트
        recent_from = self.as_of - timedelta(days=6)
        overpacing = {n for n, p in self.pacing.items() if p["pace_ratio"] - 1 > RULES["pacing_tolerance"]}
        candidates = []
        for c in self.cfg["campaigns"]:
            camp = c["campaign_name"]
            last7 = agg(self.range_rows(recent_from, self.as_of, campaign_name=camp))
            base = {"network": c["network"], "campaign": camp, "current_daily": c["daily_budget"]}
            if last7["spend"] < RULES["budget_min_spend_7d"]:
                self.proposals.append(dict(base, type="hold", proposed_daily=c["daily_budget"], change=0.0,
                                           reason=f"최근 7일 지출 {usd(last7['spend'])} — 최소 표본 {usd(RULES['budget_min_spend_7d'])} 미만, 판단 보류",
                                           guardrail="표본 부족", confidence="low"))
                continue
            conf = agg(self.range_rows(d7_from, d7_to, campaign_name=camp))
            hist = agg(self.range_rows(self.as_of - timedelta(days=20), d7_to, campaign_name=camp))
            mult = ((hist["rev_iap_d7"] + hist["rev_ad_d7"]) / rev_d0(hist)) if rev_d0(hist) else mult_fb
            target = targets[c["tier"]]
            est = rev_d0(last7) / last7["spend"] * mult
            confirmed = (conf["rev_iap_d7"] + conf["rev_ad_d7"]) / conf["spend"] if conf["spend"] else None
            ratio = est / target
            daily_ratios = []
            for i in range(RULES["budget_down_days"]):
                s = agg(self.range_rows(self.as_of - timedelta(days=i), self.as_of - timedelta(days=i), campaign_name=camp))
                daily_ratios.append(rev_d0(s) / s["spend"] * mult / target if s["spend"] else None)
            # CPI 안정성: 최근 3일 CPI vs 직전 14일 일별 CPI 중앙값
            s3 = agg(self.range_rows(self.as_of - timedelta(days=2), self.as_of, campaign_name=camp))
            prev = []
            for i in range(3, 17):
                s = agg(self.range_rows(self.as_of - timedelta(days=i), self.as_of - timedelta(days=i), campaign_name=camp))
                if s["installs"]:
                    prev.append(s["spend"] / s["installs"])
            cpi3 = s3["spend"] / s3["installs"] if s3["installs"] else None
            cpi_med = median(prev) if prev else None
            cpi_stable = cpi3 is not None and cpi_med and cpi3 <= cpi_med * (1 + RULES["cpi_stable_tolerance"])
            ch = c["last_budget_change"]
            since = (self.report_date - date.fromisoformat(ch["date"])).days
            metrics_txt = (f"추정 D7 ROAS {est * 100:.1f}%(D0×{mult:.2f}) — 목표 {target * 100:.0f}%의 {ratio * 100:.0f}%"
                           + (f", 확정 D7({md(d7_from)}~{md(d7_to)} 코호트) {confirmed * 100:.1f}%" if confirmed else ""))
            install_conf = "high" if last7["installs"] >= 300 else "medium" if last7["installs"] >= 100 else "low"
            row = dict(base, est_roas_d7=round(est, 4), confirmed_roas_d7=r2(confirmed), target_roas_d7=target,
                       vs_target=round(ratio, 3), d7_d0_multiplier=round(mult, 2),
                       spend_7d=round(last7["spend"], 2), installs_7d=int(last7["installs"]),
                       days_since_change=since, confidence=install_conf)

            want = None
            if ratio >= RULES["budget_up_ratio"]:
                want = "up"
            elif all(x is not None and x < RULES["budget_down_ratio"] for x in daily_ratios):
                want = "down"
            blockers = self.blockers(camp)
            if want == "up" and camp in self.no_increase:
                blockers += self.no_increase[camp]
            if want is None and camp in self.no_increase and not blockers:
                self.proposals.append(dict(row, type="hold", proposed_daily=c["daily_budget"], change=0.0,
                                           reason=f"{metrics_txt}. 감액 신호 없음, {', '.join(self.no_increase[camp])} — 원인 확인 전 증액 금지",
                                           guardrail="지표 이상 범위는 원인 확인 전 증액하지 않음"))
                continue
            if want == "up" and not cpi_stable:
                blockers.append(f"CPI 불안정(최근 3일 {usd(cpi3 or 0, 2)} vs 중앙값 {usd(cpi_med or 0, 2)})")
            if want == "up" and c["network"] in overpacing:
                blockers.append(f"{NETWORK_KO[c['network']]} 월 예산 소진 속도 초과")
            if want and since < RULES["budget_cooldown_days"]:
                blockers.append(f"쿨다운 — {md(date.fromisoformat(ch['date']))} 변경 후 {since}일")
            if blockers:
                self.proposals.append(dict(row, type="hold", proposed_daily=c["daily_budget"], change=0.0,
                                           reason=(f"{metrics_txt}. {'증액 신호가 있으나 보류' if want == 'up' else '감액 신호가 있으나 보류' if want == 'down' else '예산 판단 보류'}: "
                                                   f"{', '.join(blockers)}"),
                                           guardrail="데이터 문제·이상·쿨다운 범위에서는 예산 제안을 내지 않음"))
                continue
            if want == "up":
                step = RULES["budget_step_up_strong"] if ratio >= RULES["budget_up_strong_ratio"] else RULES["budget_step_up"]
                candidates.append((row, step, metrics_txt, since, ch))
            elif want == "down":
                pause = confirmed is not None and confirmed / target < RULES["budget_down_ratio"]
                candidates.append((row, RULES["budget_step_down"], metrics_txt + (", 확정 D7 도 70% 미만 — 2주 지속 시 일시 중지 검토" if pause else ""), since, ch))

        # 제로섬: 매체별로 증액분은 감액분 + 월 예산 여유분 안에서만
        for net in sorted({r[0]["network"] for r in candidates}):
            items = [x for x in candidates if x[0]["network"] == net]
            downs = [x for x in items if x[1] < 0]
            ups = [x for x in items if x[1] > 0]
            freed = sum(-round_budget(x[0]["current_daily"] * (1 + x[1]), down=True) + x[0]["current_daily"] for x in downs)
            headroom = max(0.0, self.pacing[net]["headroom_daily"])
            pool = freed + headroom
            for row, step, txt, since, ch in downs:
                new = round_budget(row["current_daily"] * (1 + step), down=True)
                self.proposals.append(dict(row, type="budget_down", proposed_daily=new,
                                           change=round(new / row["current_daily"] - 1, 3),
                                           reason=f"{txt}. 최근 {RULES['budget_down_days']}일 모두 목표의 70% 미만",
                                           guardrail=f"변경 폭 20% 이내, 마지막 변경({md(date.fromisoformat(ch['date']))}) 후 {since}일 경과"))
            for row, step, txt, since, ch in ups:
                want_new = round_budget(row["current_daily"] * (1 + step), down=False)
                add = want_new - row["current_daily"]
                note = ""
                if add > pool:
                    add = math.floor(pool / 5) * 5
                    note = f", 매체 내 재원 한도로 +{usd(add)} 로 축소"
                if add <= 0:
                    self.proposals.append(dict(row, type="hold", proposed_daily=row["current_daily"], change=0.0,
                                               reason=f"{txt}. 증액 신호가 있으나 {NETWORK_KO[net]} 월 예산 여유·감액분이 없어 보류",
                                               guardrail="월 총예산 초과 금지"))
                    continue
                pool -= add
                new = row["current_daily"] + add
                self.proposals.append(dict(row, type="budget_up", proposed_daily=new,
                                           change=round(new / row["current_daily"] - 1, 3),
                                           reason=f"{txt}. CPI 안정(최근 3일 직전 14일 중앙값 대비 +15% 이내)",
                                           guardrail=(f"변경 폭 20% 이내, 마지막 변경({md(date.fromisoformat(ch['date']))}) 후 {since}일 경과, "
                                                      f"재원 = {NETWORK_KO[net]} 감액분 {usd(freed)} + 월 예산 여유 {usd(headroom)}/일{note}")))
        order = {"budget_down": 0, "budget_up": 1, "hold": 2}
        self.proposals.sort(key=lambda p: (order[p["type"]], p["network"], p["campaign"]))
        for i, p in enumerate(self.proposals, 1):
            p["id"] = f"B{i:02d}"
        ups = [p for p in self.proposals if p["type"] == "budget_up"]
        downs = [p for p in self.proposals if p["type"] == "budget_down"]
        self.checks["budget"] = {"campaigns_evaluated": len(self.cfg["campaigns"]), "up": len(ups),
                                 "down": len(downs), "hold": len(self.proposals) - len(ups) - len(downs)}

    # 요약
    def summary(self):
        y = agg(self.day_rows(self.as_of))
        prev = agg(self.range_rows(self.as_of - timedelta(days=7), self.as_of - timedelta(days=1)))
        my, mp = metrics(y), metrics(prev)
        prev_daily_spend = prev["spend"] / 7
        blocked_scopes = {(f["campaign"], f["country"]) for f in self.findings if f["type"] == "data_issue" and f["campaign"]}
        clean = agg([r for r in self.day_rows(self.as_of) if (r["campaign_name"], r["country"]) not in blocked_scopes])
        mc = metrics(clean)
        by_network = {}
        for net in sorted({r["network"] for r in self.rows}):
            m = metrics(agg(self.day_rows(self.as_of, network=net)))
            by_network[net] = {"spend": round(m["spend"], 2), "installs": int(m["installs"]),
                               "cpi": r2(m["cpi"], 2), "roas_d0": r2(m["roas_d0"])}
        by_country = {}
        for k in sorted({r["country"] for r in self.rows}):
            m = metrics(agg(self.day_rows(self.as_of, country=k)))
            by_country[k] = {"spend": round(m["spend"], 2), "installs": int(m["installs"]),
                             "cpi": r2(m["cpi"], 2), "roas_d0": r2(m["roas_d0"])}
        ad_share = y["rev_ad_d0"] / rev_d0(y) if rev_d0(y) else None
        ups = [p for p in self.proposals if p["type"] == "budget_up"]
        downs = [p for p in self.proposals if p["type"] == "budget_down"]
        return {
            "spend": round(my["spend"], 2), "installs": int(my["installs"]), "cpi": r2(my["cpi"], 2),
            "roas_d0": r2(my["roas_d0"]), "ad_revenue_share_d0": r2(ad_share, 3),
            "vs_prev_7d": {"spend": round(my["spend"] / prev_daily_spend - 1, 3),
                           "cpi": round(my["cpi"] / mp["cpi"] - 1, 3) if my["cpi"] and mp["cpi"] else None,
                           "roas_d0": round(my["roas_d0"] / mp["roas_d0"] - 1, 3) if mp["roas_d0"] else None},
            "excluding_data_issue": {"spend": round(mc["spend"], 2), "installs": int(mc["installs"]),
                                     "cpi": r2(mc["cpi"], 2), "note": "추적 누락 의심 범위를 뺀 값"},
            "by_network": by_network, "by_country": by_country,
            "pacing": self.pacing,
            "budget_net_change_daily": round(sum(p["proposed_daily"] - p["current_daily"] for p in ups + downs), 2),
            "counts": {t: sum(1 for f in self.findings if f["type"] == t) for t in TYPE_ORDER},
        }

    def run(self):
        self.check_data()
        self.check_pacing()
        self.check_anomaly()
        self.check_fatigue()
        self.check_scale()
        self.check_budget()
        self.findings.sort(key=lambda f: (TYPE_ORDER[f["type"]], SEVERITY_ORDER[f["severity"]], f.get("campaign") or "", f.get("country") or ""))
        for i, f in enumerate(self.findings, 1):
            f["id"] = f"F{i:02d}"
        return {
            "as_of": self.as_of.isoformat(),
            "report_date": self.report_date.isoformat(),
            "is_sample_data": bool(self.cfg.get("is_sample_data")),
            "notice": "[가상 데이터] 썬더게임즈 실제 광고 데이터가 아니다." if self.cfg.get("is_sample_data") else "",
            "currency": self.cfg.get("currency", "USD"),
            "rules_version": "playbook-initial-2026-10",
            "summary": self.summary(),
            "findings": [reorder(f) for f in self.findings],
            "budget_proposals": [reorder_proposal(p) for p in self.proposals],
            "checks": self.checks,
            "near_misses": self.near_misses,
        }


def anomaly_action(drivers, peer_normal, net_ko):
    cvr, cpm = drivers.get("cvr"), drivers.get("cpm")
    tail = " 원인 확인 전 이 캠페인 증액 금지"
    if cvr is not None and cvr <= -0.2:
        if peer_normal:
            return (f"CVR 급락이 주원인이고 같은 국가 다른 매체는 정상 — {net_ko} 쪽 변화(노출 위치 구성, 최근 에셋·입찰·타깃 변경) "
                    "확인. 스토어 페이지 문제 가능성은 낮음." + tail)
        return "CVR 급락이 주원인 — 이 국가 스토어 페이지·현지화·앱 심사/배포 상태 확인." + tail
    if cpm is not None and cpm >= 0.2:
        return "CPM 상승이 주원인 — 경매 경쟁(시즌·대형 경쟁작 집행) 여부 확인, 입찰·타깃 점검." + tail
    return "CTR 하락이 주원인 — 소재 노출 구성 변화 확인(소재 피로 항목 참고)." + tail


def round_budget(x, down):
    """일 예산은 $5 단위. 감액은 내림, 증액은 올림 — 변경 폭이 규칙보다 작아지지 않게."""
    return (math.floor(x / 5) if down else math.ceil(x / 5)) * 5


def reorder(f):
    head = ["id", "type", "severity", "network", "country", "campaign", "creative", "metric", "value",
            "baseline", "change", "reason", "action", "confidence"]
    return {**{k: f.get(k) for k in head}, **{k: v for k, v in f.items() if k not in head}}


def reorder_proposal(p):
    head = ["id", "type", "network", "campaign", "current_daily", "proposed_daily", "change", "reason",
            "guardrail", "confidence"]
    return {**{k: p.get(k) for k in head}, **{k: v for k, v in p.items() if k not in head}}


def main():
    ap = argparse.ArgumentParser(description="UA 일일 성과 분석 → findings.json")
    ap.add_argument("--data", default=str(HERE / "ads_daily_sample.csv"))
    ap.add_argument("--config", default=str(HERE / "budget_config.json"))
    ap.add_argument("--as-of", default=None, help="기준일(기본: 데이터의 마지막 날짜)")
    ap.add_argument("--out", default=str(HERE / "findings.json"))
    args = ap.parse_args()

    rows = load_rows(args.data)
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    as_of = date.fromisoformat(args.as_of) if args.as_of else max(r["date"] for r in rows)
    result = Analyzer(rows, config, as_of).run()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    s = result["summary"]
    print(f"{result['notice']} 기준일 {result['as_of']} → {Path(args.out).name}")
    print(f"  어제 지출 {usd(s['spend'])} ({pct(s['vs_prev_7d']['spend'])} vs 직전 7일 평균) · 설치 {s['installs']:,} · "
          f"CPI {usd(s['cpi'], 2)} · D0 ROAS {s['roas_d0'] * 100:.1f}%")
    for f in result["findings"]:
        where = " · ".join(x for x in (f["network"], f["campaign"], f["country"], f["creative"]) if x)
        print(f"  {f['id']} [{f['severity']:<6}] {f['type']:<16} {where}")
    for p in result["budget_proposals"]:
        print(f"  {p['id']} {p['type']:<11} {p['campaign']:<20} {usd(p['current_daily'])} → {usd(p['proposed_daily'])} ({pct(p['change'])})")
    print(f"  near_misses {len(result['near_misses'])}건, checks {json.dumps(result['checks'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
