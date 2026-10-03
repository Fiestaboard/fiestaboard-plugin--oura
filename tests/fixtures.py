"""Payloads shaped like Oura API v2 ``usercollection`` responses.

Field names follow Oura's published OpenAPI schema (openapi-1.41.json):
``PublicDailySleep``, ``PublicDailyReadiness``, ``PublicDailyActivity`` and
``PublicModifiedSleepModel``. Every list endpoint answers
``{"data": [...], "next_token": null}``. All values are invented.
"""

TODAY = "2026-10-02"
YESTERDAY = "2026-10-01"


def page(*docs):
    return {"data": list(docs), "next_token": None}


def daily_sleep(day=TODAY, score=82):
    return {
        "id": f"ds-{day}",
        "contributors": {
            "deep_sleep": 90,
            "efficiency": 88,
            "latency": 80,
            "rem_sleep": 85,
            "restfulness": 70,
            "timing": 95,
            "total_sleep": 84,
        },
        "day": day,
        "score": score,
        "timestamp": f"{day}T00:00:00+00:00",
    }


def daily_readiness(day=TODAY, score=76, temperature_deviation=0.21):
    return {
        "id": f"dr-{day}",
        "contributors": {"resting_heart_rate": 90, "hrv_balance": 70},
        "day": day,
        "score": score,
        "temperature_deviation": temperature_deviation,
        "temperature_trend_deviation": 0.05,
        "timestamp": f"{day}T00:00:00+00:00",
    }


def daily_activity(day=TODAY, score=91, steps=8423, active_calories=412, total_calories=2310):
    return {
        "id": f"da-{day}",
        "active_calories": active_calories,
        "contributors": {"meet_daily_targets": 100, "stay_active": 80},
        "day": day,
        "score": score,
        "steps": steps,
        "total_calories": total_calories,
        "timestamp": f"{day}T04:00:00-07:00",
    }


def sleep_period(
    day=TODAY,
    kind="long_sleep",
    total=27120,  # 7h 32m
    in_bed=29100,  # 8h 05m
    lowest_hr=52,
    average_hr=57.6,
    hrv=48,
    efficiency=93,
    start="2026-10-01T22:48:00-07:00",
    end="2026-10-02T06:53:00-07:00",
):
    return {
        "id": f"sp-{day}-{kind}",
        "average_breath": 14.5,
        "average_heart_rate": average_hr,
        "average_hrv": hrv,
        "awake_time": 1980,
        "bedtime_end": end,
        "bedtime_start": start,
        "day": day,
        "deep_sleep_duration": 5400,  # 1h 30m
        "efficiency": efficiency,
        "latency": 600,
        "light_sleep_duration": 15120,
        "low_battery_alert": False,
        "lowest_heart_rate": lowest_hr,
        "period": 0,
        "rem_sleep_duration": 6600,  # 1h 50m
        "restless_periods": 210,
        "time_in_bed": in_bed,
        "total_sleep_duration": total,
        "type": kind,
    }
