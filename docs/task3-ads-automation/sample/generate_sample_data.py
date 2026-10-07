#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[가상 데이터 · SAMPLE ONLY] 「원시인 형님들 키우기」 UA 일일 성과 — 가상 광고 데이터 생성기

이 스크립트가 만드는 숫자는 전부 지어낸 값이다. 썬더게임즈(T.G Inc)의 실제 광고 계정·MMP·예산과
관계가 없다. 매체 구성(Meta 4 + Google 4 캠페인), 국가(KR·US·BR·MX·ID·TH — 리뷰 언어 분포를 대리
지표로 고른 가정), 일 예산, 목표 ROAS 는 설명을 위한 가정이다.

analyze_ads.py 가 잡아내야 할 문제를 일부러 심어 두었다.
  P1 추적 누락 의심    META_A+APP_IOS_T1 · KR·US — 10/04~10/06 지출·클릭은 정상인데 설치 0
  P2 국가 CPI 급등     GOOG_ACi_AOS_T3 · ID — 10/05~10/06 CVR −45%, CPM +25% 설정 (CPI 약 2.3배)
  P3 소재 피로(Meta)   META_A+APP_AOS_T2 · BR · MV03_tower_climb_15s — 09/20부터 CTR 이 떨어져 끝에는 −37%, frequency 약 3.9 (설정값)
  P4 소재 피로(Google) GOOG_ACi_AOS_T1 · GV02_gear_merge_15s (캠페인 전체) — 09/17부터 IPM 이 서서히 떨어져 끝에는 약 −44% (설정값, Google 은 frequency 없음)
  P5 지출 속도 초과    Google 10월 예산 — 10/01 GOOG_ACi_AOS_T1 일 예산을 $330→$400 로 올린 뒤 월 계획 미갱신
  P6 확장 후보 소재    META_A+APP_AOS_T1 · MP01_totem_tap_playable — IPM 이 캠페인 중앙값의 약 1.6배
  P7 예산 신호         M1 목표 대비 D7 ROAS 약 140%(증액), M3 약 55%(감액),
                       G2 약 130%이지만 10/05 에 예산을 바꿔 쿨다운 중(보류), G4 는 표본 부족(판단 보류)
  미끼(잡히면 안 됨)   META_A+APP_AOS_T3 · ID · MV02 — CTR −28% 설정이지만 frequency 1.9 → 피로 규칙 미충족

출력: ads_daily_sample.csv (일 × 매체 × 캠페인 × 국가 × 소재, kpi-framework 표준 컬럼)
      budget_config.json   (월 예산·일 예산·마지막 예산 변경일·국가 Tier 목표 ROAS)
실행: python3 generate_sample_data.py   — 표준 라이브러리만 쓴다. 시드 고정이라 항상 같은 파일이 나온다.

단순화한 점(실제 데이터와 다른 곳)
  - Google 앱 캠페인은 원래 '에셋' 단위 성과만 주고, 에셋 지표를 더해도 캠페인 합계와 맞지 않는다.
    샘플에서는 설명을 위해 에셋 행을 더하면 캠페인 합계가 되도록 만들었다.
  - Google 에셋 성과는 국가 분할을 전제하지 않고([확인 필요]), analyze_ads.py 는 Google 소재를 캠페인 단위로 판정한다.
  - frequency 는 Meta 가 보고하는 최근 7일 누적값(도달 기반, 더하면 안 되는 값)으로 행마다 넣었다.
  - installs 는 MMP 기준이라고 가정한다. iOS 는 실제로는 SKAdNetwork 집계·지연이 있다.
"""
import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20261007
AS_OF = date(2026, 10, 6)            # 데이터 기준일(어제). 리포트는 2026-10-07 아침에 나간다
DAYS = 28
START = AS_OF - timedelta(days=DAYS - 1)
HERE = Path(__file__).resolve().parent
D7_D0_MULT = 2.9                     # 가상: D7 누적 수익 / D0 수익

TARGET_ROAS_D7 = {"T1": 0.10, "T2": 0.09, "T3": 0.08}   # 가상 목표 — 실제는 자사 28일 중앙값으로 정한다

# 국가 기본값: tier, CVR(설치/클릭), CPM(Meta·Google, USD), D1 잔존율, 수익 중 광고 비중
COUNTRIES = {
    "KR": dict(tier="T1", cvr=0.30, cpm={"meta": 13.0, "google": 9.5}, d1=0.40, ad_share=0.35),
    "US": dict(tier="T1", cvr=0.28, cpm={"meta": 16.0, "google": 11.5}, d1=0.36, ad_share=0.45),
    "BR": dict(tier="T2", cvr=0.36, cpm={"meta": 4.2, "google": 3.0}, d1=0.33, ad_share=0.60),
    "MX": dict(tier="T2", cvr=0.34, cpm={"meta": 4.6, "google": 3.3}, d1=0.34, ad_share=0.55),
    "ID": dict(tier="T3", cvr=0.40, cpm={"meta": 1.9, "google": 1.3}, d1=0.30, ad_share=0.65),
    "TH": dict(tier="T3", cvr=0.38, cpm={"meta": 2.6, "google": 1.9}, d1=0.31, ad_share=0.60),
}
IOS_CPM_MULT = 1.45                  # iOS 경매가 더 비싸다(가정)

# 소재: 이름, 형식, 기본 CTR, CVR 배수, 영상 3초 조회율(hook), 첫 노출일
# 게임 안 시스템(보스토벌·장비 합성·형님의 탑·문명·토템·슬롯머신)을 소재 주제로 삼았다(가상 소재).
CREATIVES = {
    "MV01": ("MV01_boss_raid_15s", "video", 0.0120, 1.00, 0.27, None),
    "MV02": ("MV02_gear_merge_20s", "video", 0.0115, 1.00, 0.25, None),
    "MV03": ("MV03_tower_climb_15s", "video", 0.0140, 1.00, 0.31, date(2026, 9, 15)),
    "MI01": ("MI01_caveman_bros_static", "image", 0.0105, 0.95, None, None),
    "MI02": ("MI02_civ_evolution_static", "image", 0.0110, 0.95, None, None),
    "MP01": ("MP01_totem_tap_playable", "playable", 0.0150, 1.25, None, date(2026, 9, 14)),
    "GV01": ("GV01_boss_raid_30s", "video", 0.0092, 1.00, None, None),
    "GV02": ("GV02_gear_merge_15s", "video", 0.0090, 1.00, None, None),
    "GV03": ("GV03_civ_timelapse_20s", "video", 0.0088, 1.00, None, None),
    "GI01": ("GI01_bros_banner_1200x628", "image", 0.0085, 0.97, None, None),
    "GI02": ("GI02_slot_bonus_1200x628", "image", 0.0087, 0.97, None, None),
    "GH01": ("GH01_totem_tap_html5", "playable", 0.0098, 1.05, None, None),
}

# 캠페인: 예산 이력은 (적용 시작일, 일 예산). quality 는 '목표 대비 D7 ROAS' 배수(가상)
CAMPAIGNS = [
    dict(key="m1", id="120214500000101", name="META_A+APP_AOS_T1", network="meta", type="advantage_plus_app",
         platform="android", countries={"KR": 0.45, "US": 0.55}, creatives=["MV01", "MV02", "MI01", "MP01"],
         budget=[(date(2026, 9, 1), 350)], quality=1.40),
    dict(key="m2", id="120214500000102", name="META_A+APP_AOS_T2", network="meta", type="advantage_plus_app",
         platform="android", countries={"BR": 0.60, "MX": 0.40}, creatives=["MV01", "MV02", "MV03", "MI01"],
         budget=[(date(2026, 9, 1), 280)], quality=0.97),
    dict(key="m3", id="120214500000103", name="META_A+APP_AOS_T3", network="meta", type="advantage_plus_app",
         platform="android", countries={"ID": 0.55, "TH": 0.45}, creatives=["MV01", "MV02", "MI02"],
         budget=[(date(2026, 9, 1), 300), (date(2026, 9, 28), 250)], quality=0.55),
    dict(key="m4", id="120214500000104", name="META_A+APP_IOS_T1", network="meta", type="advantage_plus_app",
         platform="ios", countries={"KR": 0.40, "US": 0.60}, creatives=["MV01", "MV02", "MI01"],
         budget=[(date(2026, 9, 1), 200)], quality=0.95),
    dict(key="g1", id="21987650001", name="GOOG_ACi_AOS_T1", network="google", type="app_campaign_install",
         platform="android", countries={"KR": 0.45, "US": 0.55}, creatives=["GV01", "GV02", "GI01", "GH01"],
         budget=[(date(2026, 9, 1), 330), (date(2026, 10, 1), 400)], quality=1.02),
    dict(key="g2", id="21987650002", name="GOOG_ACi_AOS_T2", network="google", type="app_campaign_install",
         platform="android", countries={"BR": 0.60, "MX": 0.40}, creatives=["GV01", "GV03", "GI01"],
         budget=[(date(2026, 9, 1), 200), (date(2026, 10, 5), 220)], quality=1.30),
    dict(key="g3", id="21987650003", name="GOOG_ACi_AOS_T3", network="google", type="app_campaign_install",
         platform="android", countries={"ID": 0.55, "TH": 0.45}, creatives=["GV01", "GV02", "GI02"],
         budget=[(date(2026, 9, 1), 250)], quality=0.95),
    dict(key="g4", id="21987650004", name="GOOG_ACi_IOS_T1", network="google", type="app_campaign_install",
         platform="ios", countries={"KR": 0.40, "US": 0.60}, creatives=["GV01", "GI01"],
         budget=[(date(2026, 9, 1), 25)], quality=1.00),
]

MONTHLY_BUDGET = {"meta": 33800, "google": 22500}   # 2026-10 가상 월 예산(USD)


def daily_budget(camp, d):
    amount = camp["budget"][0][1]
    for start, value in camp["budget"]:
        if d >= start:
            amount = value
    return amount


def ramp(age, start, end, floor):
    """age 가 start 일 때 1.0 → end 일 때 floor 로 선형 감소, 이후 floor 유지."""
    if age <= start:
        return 1.0
    if age >= end:
        return floor
    return 1.0 - (1.0 - floor) * (age - start) / (end - start)


def effects(camp, country, cr, d, age):
    """심어 둔 문제(P1~P4, 미끼)의 배수. 그 밖의 행은 모두 1.0."""
    e = {"ctr": 1.0, "cvr": 1.0, "cpm": 1.0, "freq": 1.35 + 0.02 * min(age, 20), "zero_installs": False}
    key = camp["key"]
    if key == "m4" and d >= date(2026, 10, 4):                      # P1 추적 누락
        e["zero_installs"] = True
    if key == "g3" and country == "ID" and d >= date(2026, 10, 5):  # P2 CPI 급등
        e["cvr"], e["cpm"] = 0.55, 1.25
    if key == "m2" and cr == "MV03":                                # P3 소재 피로(BR 강, MX 약)
        if country == "BR":
            e["ctr"], e["freq"] = ramp(age, 5, 21, 0.63), 1.30 + 0.125 * age
        else:
            e["ctr"], e["freq"] = ramp(age, 5, 21, 0.84), 1.30 + 0.058 * age
    if key == "g1" and cr == "GV02":                                # P4 Google 소재 피로(IPM, 캠페인 전체)
        e["ctr"], e["cvr"] = ramp(age, 8, 27, 0.75), ramp(age, 8, 27, 0.75)
    if key == "m3" and country == "ID" and cr == "MV02":            # 미끼: CTR 만 하락, frequency 낮음
        e["ctr"], e["freq"] = ramp(age, 0, 27, 0.72), 1.50 + 0.015 * age
    return e


def noisy_count(mu, rng):
    """평균 mu 의 계수 잡음(포아송 근사)."""
    if mu <= 0:
        return 0
    if mu < 30:
        # 작은 수는 포아송 직접 추출(Knuth)
        limit, k, p = math.exp(-mu), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, int(round(rng.gauss(mu, math.sqrt(mu)))))


def base_cpi(camp, country):
    """심어 둔 문제가 없을 때의 캠페인×국가 기대 CPI — 수익(ARPU) 수준을 정하는 기준."""
    c = COUNTRIES[country]
    cpm = c["cpm"][camp["network"]] * (IOS_CPM_MULT if camp["platform"] == "ios" else 1.0)
    shares = [1.0 / len(camp["creatives"])] * len(camp["creatives"])
    ipm = sum(s * CREATIVES[k][2] * CREATIVES[k][3] for s, k in zip(shares, camp["creatives"])) * c["cvr"] * 1000
    return cpm / ipm


def main():
    rng = random.Random(SEED)
    rows = []
    for i in range(DAYS):
        d = START + timedelta(days=i)
        for camp in CAMPAIGNS:
            spend_total = daily_budget(camp, d) * rng.gauss(1.0, 0.035)
            for country, cshare in camp["countries"].items():
                c = COUNTRIES[country]
                active = [k for k in camp["creatives"] if CREATIVES[k][5] is None or d >= CREATIVES[k][5]]
                weights = {k: 1.0 for k in active}
                # ARPU 를 기대 CPI 에 묶어 두면 캠페인의 D7 ROAS 가 '목표 × quality' 근처에 모인다.
                # iOS 는 CPI 가 비싼 만큼 ARPU 도 높다고 가정한 셈이다.
                arpu_d7 = TARGET_ROAS_D7[c["tier"]] * base_cpi(camp, country) * camp["quality"]
                for cr in active:
                    name, fmt, ctr0, cvr_mult, hook0, launch = CREATIVES[cr]
                    age = (d - (launch or START)).days
                    e = effects(camp, country, cr, d, age)
                    spend = spend_total * cshare * weights[cr] / sum(weights.values()) * rng.gauss(1.0, 0.05)
                    cpm = c["cpm"][camp["network"]] * (IOS_CPM_MULT if camp["platform"] == "ios" else 1.0)
                    cpm *= e["cpm"] * rng.gauss(1.0, 0.05)
                    impressions = int(spend / cpm * 1000)
                    clicks = noisy_count(impressions * ctr0 * e["ctr"], rng)
                    installs = 0 if e["zero_installs"] else noisy_count(clicks * c["cvr"] * cvr_mult * e["cvr"], rng)

                    video_3s = ""
                    frequency = ""
                    if camp["network"] == "meta":
                        frequency = round(e["freq"] * rng.gauss(1.0, 0.03), 2)
                        if fmt == "video":
                            hook = hook0 * (0.7 + 0.3 * e["ctr"]) * rng.gauss(1.0, 0.03)
                            video_3s = int(impressions * hook)

                    days_old = (AS_OF - d).days
                    d1 = round(installs * c["d1"] * rng.gauss(1.0, 0.05)) if days_old >= 1 else ""
                    d7 = round(installs * c["d1"] * 0.38 * rng.gauss(1.0, 0.07)) if days_old >= 7 else ""

                    sigma = 0.05 + 0.5 / math.sqrt(max(installs, 1))
                    rev_noise = rng.lognormvariate(0, sigma) / math.exp(sigma ** 2 / 2)
                    rev_d0 = installs * arpu_d7 / D7_D0_MULT * rev_noise
                    mult = D7_D0_MULT * rng.gauss(1.0, 0.06)
                    ad_share = c["ad_share"] * rng.gauss(1.0, 0.04)
                    rev_iap_d0 = round(rev_d0 * (1 - ad_share), 2)
                    rev_ad_d0 = round(rev_d0 * ad_share, 2)
                    rev_iap_d7 = round(rev_iap_d0 * mult * rng.gauss(1.0, 0.04), 2) if days_old >= 7 else ""
                    rev_ad_d7 = round(rev_ad_d0 * mult * rng.gauss(1.0, 0.04), 2) if days_old >= 7 else ""

                    rows.append({
                        "date": d.isoformat(), "network": camp["network"], "campaign_id": camp["id"],
                        "campaign_name": camp["name"], "campaign_type": camp["type"], "country": country,
                        "platform": camp["platform"], "creative_id": cr, "creative_name": name,
                        "creative_format": fmt, "spend": round(spend, 2), "impressions": impressions,
                        "clicks": clicks, "installs": installs, "video_3s_views": video_3s,
                        "frequency": frequency, "d1_retained": d1, "d7_retained": d7,
                        "rev_iap_d0": rev_iap_d0, "rev_iap_d7": rev_iap_d7,
                        "rev_ad_d0": rev_ad_d0, "rev_ad_d7": rev_ad_d7,
                    })

    csv_path = HERE / "ads_daily_sample.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        f.write("# [가상 데이터] 썬더게임즈 실제 광고 데이터가 아님 — generate_sample_data.py (seed=%d) 로 생성\n" % SEED)
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    config = {
        "_notice": "[가상 데이터] 예산·목표값은 설명용 가정이다. 실제 값은 썬더게임즈 확인 대상[확인 필요].",
        "is_sample_data": True,
        "currency": "USD",
        "timezone": "Asia/Seoul",
        "month": AS_OF.strftime("%Y-%m"),
        "monthly_budget": MONTHLY_BUDGET,
        "country_tier": {k: v["tier"] for k, v in COUNTRIES.items()},
        "target_roas_d7": TARGET_ROAS_D7,
        "d7_d0_multiplier_fallback": D7_D0_MULT,
        "campaigns": [],
    }
    for camp in CAMPAIGNS:
        hist = camp["budget"]
        change = None
        if len(hist) > 1:
            change = {"date": hist[-1][0].isoformat(), "from": hist[-2][1], "to": hist[-1][1]}
        else:
            change = {"date": hist[0][0].isoformat(), "from": None, "to": hist[0][1]}
        config["campaigns"].append({
            "campaign_id": camp["id"], "campaign_name": camp["name"], "network": camp["network"],
            "platform": camp["platform"], "tier": COUNTRIES[next(iter(camp["countries"]))]["tier"],
            "daily_budget": daily_budget(camp, AS_OF), "last_budget_change": change,
        })
    with open(HERE / "budget_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print("[가상 데이터] %d행 생성: %s ~ %s, 캠페인 %d개 → %s, budget_config.json"
          % (len(rows), START.isoformat(), AS_OF.isoformat(), len(CAMPAIGNS), csv_path.name))


if __name__ == "__main__":
    main()
