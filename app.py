import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from crewai import Agent, Crew, Process, Task
from crewai.tools import tool
from json_repair import repair_json


st.set_page_config(
    page_title="RunLens AI",
    page_icon="🏃",
    layout="wide",
)

def get_setting(name, default=""):
    """Lê ambiente ou st.secrets sem expor credenciais na interface."""
    environment_value = os.getenv(name)
    if environment_value:
        return environment_value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# No Colab, RUNLENS_DB_PATH aponta para o Google Drive. No Streamlit Cloud,
# o caminho relativo mantém o protótipo autocontido (sem garantia de
# persistência após reinicializações do serviço).
DB_PATH = get_setting("RUNLENS_DB_PATH", "data/runlens.db")
GEMINI_API_KEY = get_setting("GEMINI_API_KEY", "")
STRAVA_ACCESS_TOKEN = get_setting("STRAVA_ACCESS_TOKEN", "")
GEMINI_MODEL_ID = get_setting(
    "GEMINI_MODEL_ID",
    "gemini-3.1-flash-lite",
).removeprefix("gemini/")
CREWAI_MODEL = f"gemini/{GEMINI_MODEL_ID}"
STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
RUN_SPORT_TYPES = {"Run", "TrailRun", "VirtualRun"}

if GEMINI_API_KEY:
    os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
    os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def get_db_connection():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    with get_db_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS athletes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                race_name TEXT NOT NULL,
                race_distance_km REAL NOT NULL,
                race_date TEXT NOT NULL,
                target_time_minutes REAL NOT NULL,
                available_days_per_week INTEGER NOT NULL,
                notes TEXT,
                target_pace_min_km REAL NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                external_activity_id TEXT,
                data_source TEXT NOT NULL,
                activity_name TEXT NOT NULL,
                sport_type TEXT NOT NULL,
                start_date_local TEXT NOT NULL,
                distance_m REAL NOT NULL,
                moving_time_seconds REAL NOT NULL,
                elapsed_time_seconds REAL,
                elevation_gain_m REAL,
                average_speed_mps REAL,
                average_heartrate REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id) ON DELETE CASCADE,
                UNIQUE (athlete_id, data_source, external_activity_id)
            );

            CREATE TABLE IF NOT EXISTS weekly_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                planned_km REAL NOT NULL,
                planned_sessions INTEGER NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (athlete_id) REFERENCES athletes(id) ON DELETE CASCADE,
                UNIQUE (athlete_id, week_start)
            );
            """
        )


def list_athletes():
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT athletes.id, athletes.athlete_name
            FROM athletes
            INNER JOIN goals ON goals.athlete_id = athletes.id
            WHERE goals.is_active = 1
            ORDER BY athletes.athlete_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_goal(
    athlete_name,
    race_name,
    race_distance_km,
    race_date,
    target_time_minutes,
    available_days_per_week,
    notes="",
):
    athlete_name = athlete_name.strip()
    race_name = race_name.strip()
    if not athlete_name or not race_name:
        raise ValueError("Informe o nome do atleta e da prova.")
    if race_date is None or race_date <= datetime.now(timezone.utc).date():
        raise ValueError("A data da prova deve estar no futuro.")
    if race_distance_km <= 0 or target_time_minutes <= 0:
        raise ValueError("Distância e tempo-alvo devem ser maiores que zero.")
    if not 1 <= available_days_per_week <= 7:
        raise ValueError("Os dias disponíveis devem estar entre 1 e 7.")

    target_pace = float(target_time_minutes) / float(race_distance_km)
    with get_db_connection() as connection:
        athlete = connection.execute(
            """
            SELECT id FROM athletes
            WHERE LOWER(TRIM(athlete_name)) = LOWER(?)
            LIMIT 1
            """,
            (athlete_name,),
        ).fetchone()
        if athlete is None:
            cursor = connection.execute(
                "INSERT INTO athletes (athlete_name) VALUES (?)",
                (athlete_name,),
            )
            athlete_id = cursor.lastrowid
        else:
            athlete_id = athlete["id"]
            connection.execute(
                "UPDATE athletes SET athlete_name = ? WHERE id = ?",
                (athlete_name, athlete_id),
            )

        connection.execute(
            """
            UPDATE goals
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE athlete_id = ?
            """,
            (athlete_id,),
        )
        connection.execute(
            """
            INSERT INTO goals (
                athlete_id, race_name, race_distance_km, race_date,
                target_time_minutes, available_days_per_week, notes,
                target_pace_min_km, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                athlete_id,
                race_name,
                float(race_distance_km),
                race_date.isoformat(),
                float(target_time_minutes),
                int(available_days_per_week),
                notes.strip(),
                target_pace,
            ),
        )
    return int(athlete_id)


def load_athlete_goal(athlete_id):
    if not athlete_id:
        return None
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT goals.id AS goal_id, goals.athlete_id,
                   athletes.athlete_name, goals.race_name,
                   goals.race_distance_km, goals.race_date,
                   goals.target_time_minutes, goals.available_days_per_week,
                   goals.notes, goals.target_pace_min_km
            FROM goals
            INNER JOIN athletes ON athletes.id = goals.athlete_id
            WHERE goals.athlete_id = ? AND goals.is_active = 1
            ORDER BY goals.id DESC LIMIT 1
            """,
            (int(athlete_id),),
        ).fetchone()
    if row is None:
        return None
    goal = dict(row)
    event_date = datetime.strptime(goal["race_date"], "%Y-%m-%d").date()
    goal["days_to_race"] = (event_date - datetime.now(timezone.utc).date()).days
    goal["weeks_to_race"] = round(goal["days_to_race"] / 7, 1)
    goal["target_pace_label"] = format_pace(goal["target_pace_min_km"])
    return goal


def load_strava_activities(access_token, lookback_days=90):
    if not access_token:
        raise ValueError("Informe um token de acesso do Strava.")
    after = int(
        (datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp()
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    for page in range(1, 11):
        response = requests.get(
            f"{STRAVA_API_BASE_URL}/athlete/activities",
            headers=headers,
            params={"after": after, "page": page, "per_page": 100},
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < 100:
            break

    running = []
    for activity in activities:
        sport_type = activity.get("sport_type") or activity.get("type")
        if sport_type in RUN_SPORT_TYPES:
            item = dict(activity)
            item["data_source"] = "strava"
            running.append(item)
    return running


def make_demo_activities(weeks=12, seed=42):
    rng = np.random.default_rng(seed)
    today = pd.Timestamp.today().normalize()
    current_week = today - pd.Timedelta(days=today.weekday())
    week_starts = pd.date_range(
        end=current_week - pd.Timedelta(days=7),
        periods=int(weeks),
        freq="7D",
    )
    activities = []
    activity_number = 100_000
    for week_index, week in enumerate(week_starts):
        sessions = 3 if week_index < 4 else 4
        progressive_base = 5.0 + 0.13 * week_index
        for session_index, offset in enumerate([0, 2, 4, 6][:sessions]):
            if session_index == sessions - 1:
                distance_km = 8.0 + 0.55 * week_index + rng.normal(0, 0.5)
                workout_name = "Longo"
                pace = 6.25 - 0.015 * week_index + rng.normal(0, 0.08)
            elif session_index == 1 and week_index >= 3:
                distance_km = progressive_base + rng.normal(0, 0.35)
                workout_name = "Ritmo/intervalado"
                pace = 5.35 - 0.025 * week_index + rng.normal(0, 0.08)
            else:
                distance_km = progressive_base + rng.normal(0, 0.4)
                workout_name = "Corrida leve"
                pace = 6.05 - 0.012 * week_index + rng.normal(0, 0.1)

            distance_km = max(float(distance_km), 3.0)
            moving_time = distance_km * pace * 60
            start = week + pd.Timedelta(days=offset, hours=7)
            activities.append(
                {
                    "id": f"demo-{activity_number}",
                    "name": workout_name,
                    "sport_type": "Run",
                    "start_date_local": start.isoformat(),
                    "distance": distance_km * 1000,
                    "moving_time": int(moving_time),
                    "elapsed_time": int(moving_time + rng.integers(0, 240)),
                    "total_elevation_gain": float(max(0, rng.normal(55, 20))),
                    "average_speed": float((distance_km * 1000) / moving_time),
                    "average_heartrate": float(
                        148 + (6.0 - pace) * 8 + rng.normal(0, 3)
                    ),
                    "data_source": "demo",
                }
            )
            activity_number += 1
    return activities


def save_activities(athlete_id, activities, data_source, replace_source=False):
    source = data_source.strip().lower()
    with get_db_connection() as connection:
        if replace_source:
            connection.execute(
                "DELETE FROM activities WHERE athlete_id = ? AND data_source = ?",
                (int(athlete_id), source),
            )
        for activity in activities:
            external_id = activity.get("id")
            if external_id is None:
                continue
            connection.execute(
                """
                INSERT INTO activities (
                    athlete_id, external_activity_id, data_source,
                    activity_name, sport_type, start_date_local,
                    distance_m, moving_time_seconds, elapsed_time_seconds,
                    elevation_gain_m, average_speed_mps, average_heartrate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (athlete_id, data_source, external_activity_id)
                DO UPDATE SET
                    activity_name = excluded.activity_name,
                    sport_type = excluded.sport_type,
                    start_date_local = excluded.start_date_local,
                    distance_m = excluded.distance_m,
                    moving_time_seconds = excluded.moving_time_seconds,
                    elapsed_time_seconds = excluded.elapsed_time_seconds,
                    elevation_gain_m = excluded.elevation_gain_m,
                    average_speed_mps = excluded.average_speed_mps,
                    average_heartrate = excluded.average_heartrate
                """,
                (
                    int(athlete_id),
                    str(external_id),
                    source,
                    activity.get("name", "Treino sem nome"),
                    activity.get("sport_type") or activity.get("type") or "Run",
                    activity.get("start_date_local"),
                    float(activity.get("distance", 0)),
                    float(activity.get("moving_time", 0)),
                    activity.get("elapsed_time"),
                    activity.get("total_elevation_gain"),
                    activity.get("average_speed"),
                    activity.get("average_heartrate"),
                ),
            )
    return len(activities)


def normalize_activities(activities):
    columns = [
        "id",
        "name",
        "sport_type",
        "start_date_local",
        "distance",
        "moving_time",
        "elapsed_time",
        "total_elevation_gain",
        "average_speed",
        "average_heartrate",
        "data_source",
    ]
    frame = pd.DataFrame(activities)
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[columns].copy()
    frame["date"] = pd.to_datetime(
        frame["start_date_local"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    frame["distance_km"] = pd.to_numeric(frame["distance"], errors="coerce") / 1000
    frame["moving_time_sec"] = pd.to_numeric(
        frame["moving_time"], errors="coerce"
    )
    frame["duration_min"] = frame["moving_time_sec"] / 60
    frame["elevation_m"] = pd.to_numeric(
        frame["total_elevation_gain"], errors="coerce"
    )
    frame["average_speed_mps"] = pd.to_numeric(
        frame["average_speed"], errors="coerce"
    )
    frame["average_hr"] = pd.to_numeric(
        frame["average_heartrate"], errors="coerce"
    )
    frame["pace_min_km"] = np.where(
        (frame["distance_km"] > 0) & (frame["duration_min"] > 0),
        frame["duration_min"] / frame["distance_km"],
        np.nan,
    )
    frame["week"] = frame["date"].dt.normalize() - pd.to_timedelta(
        frame["date"].dt.weekday, unit="D"
    )
    frame["is_valid_activity"] = (
        frame["date"].notna()
        & (frame["distance_km"] > 0)
        & (frame["duration_min"] > 0)
    )
    return frame.sort_values("date").reset_index(drop=True)


def load_athlete_activities(athlete_id):
    if not athlete_id:
        return normalize_activities([])
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT external_activity_id AS id, activity_name AS name,
                   sport_type, start_date_local, distance_m AS distance,
                   moving_time_seconds AS moving_time,
                   elapsed_time_seconds AS elapsed_time,
                   elevation_gain_m AS total_elevation_gain,
                   average_speed_mps AS average_speed,
                   average_heartrate, data_source
            FROM activities WHERE athlete_id = ? ORDER BY start_date_local
            """,
            (int(athlete_id),),
        ).fetchall()
    return normalize_activities([dict(row) for row in rows])


def calculate_data_quality(runs):
    if runs.empty:
        return {
            "sources": [],
            "runs_received": 0,
            "valid_runs": 0,
            "invalid_runs": 0,
            "period_start": None,
            "period_end": None,
            "heart_rate_coverage_pct": 0.0,
            "elevation_coverage_pct": 0.0,
            "valid_pace_coverage_pct": 0.0,
        }
    valid_dates = runs["date"].dropna()
    valid_runs = int(runs["is_valid_activity"].sum())
    return {
        "sources": sorted(
            runs["data_source"].dropna().astype(str).unique().tolist()
        ),
        "runs_received": int(len(runs)),
        "valid_runs": valid_runs,
        "invalid_runs": int(len(runs) - valid_runs),
        "period_start": (
            valid_dates.min().date().isoformat() if not valid_dates.empty else None
        ),
        "period_end": (
            valid_dates.max().date().isoformat() if not valid_dates.empty else None
        ),
        "heart_rate_coverage_pct": round(
            100 * runs["average_hr"].notna().mean(), 1
        ),
        "elevation_coverage_pct": round(
            100 * runs["elevation_m"].notna().mean(), 1
        ),
        "valid_pace_coverage_pct": round(
            100 * runs["pace_min_km"].notna().mean(), 1
        ),
    }


def normalize_weekly_plan(plan):
    empty = pd.DataFrame(
        columns=["week", "planned_km", "planned_sessions", "notes"]
    )
    if plan is None or plan.empty:
        return empty
    normalized = plan.copy()
    if "week_start" in normalized.columns:
        normalized = normalized.rename(columns={"week_start": "week"})
    required = ["week", "planned_km", "planned_sessions"]
    missing = set(required) - set(normalized.columns)
    if missing:
        raise ValueError("Colunas ausentes: " + ", ".join(sorted(missing)))
    normalized = normalized.dropna(how="all", subset=required)
    if normalized.empty:
        return empty
    if "notes" not in normalized.columns:
        normalized["notes"] = ""
    normalized["week"] = pd.to_datetime(
        normalized["week"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    normalized["week"] = normalized["week"].dt.normalize() - pd.to_timedelta(
        normalized["week"].dt.weekday, unit="D"
    )
    normalized["planned_km"] = pd.to_numeric(
        normalized["planned_km"], errors="coerce"
    )
    normalized["planned_sessions"] = pd.to_numeric(
        normalized["planned_sessions"], errors="coerce"
    )
    if normalized["week"].isna().any():
        raise ValueError("O plano contém datas inválidas.")
    if normalized["planned_km"].isna().any() or (
        normalized["planned_km"] < 0
    ).any():
        raise ValueError("A quilometragem planejada deve ser válida e não negativa.")
    invalid_sessions = (
        normalized["planned_sessions"].isna()
        | (normalized["planned_sessions"] < 0)
        | (normalized["planned_sessions"] > 7)
    )
    if invalid_sessions.any():
        raise ValueError("As sessões planejadas devem estar entre 0 e 7.")
    normalized["planned_sessions"] = normalized["planned_sessions"].astype(int)
    normalized["notes"] = normalized["notes"].fillna("").astype(str)
    return (
        normalized[["week", "planned_km", "planned_sessions", "notes"]]
        .drop_duplicates("week", keep="last")
        .sort_values("week")
        .reset_index(drop=True)
    )


def save_weekly_plan(athlete_id, plan, replace_existing=True):
    normalized = normalize_weekly_plan(plan)
    if normalized.empty:
        raise ValueError("Preencha pelo menos uma semana do plano.")
    with get_db_connection() as connection:
        if replace_existing:
            connection.execute(
                "DELETE FROM weekly_plans WHERE athlete_id = ?",
                (int(athlete_id),),
            )
        for _, row in normalized.iterrows():
            connection.execute(
                """
                INSERT INTO weekly_plans (
                    athlete_id, week_start, planned_km,
                    planned_sessions, notes
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (athlete_id, week_start)
                DO UPDATE SET
                    planned_km = excluded.planned_km,
                    planned_sessions = excluded.planned_sessions,
                    notes = excluded.notes,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(athlete_id),
                    row["week"].date().isoformat(),
                    float(row["planned_km"]),
                    int(row["planned_sessions"]),
                    row["notes"],
                ),
            )
    return len(normalized)


def load_weekly_plan(athlete_id):
    if not athlete_id:
        return normalize_weekly_plan(pd.DataFrame())
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT week_start AS week, planned_km,
                   planned_sessions, notes
            FROM weekly_plans WHERE athlete_id = ? ORDER BY week_start
            """,
            (int(athlete_id),),
        ).fetchall()
    return normalize_weekly_plan(pd.DataFrame([dict(row) for row in rows]))


def calculate_weekly_metrics(runs):
    columns = [
        "week",
        "actual_km",
        "actual_sessions",
        "duration_min",
        "elevation_m",
        "longest_run_km",
        "pace_min_km",
        "volume_change_pct",
    ]
    if runs is None or runs.empty:
        return pd.DataFrame(columns=columns)
    valid = runs[runs["is_valid_activity"]].copy()
    if valid.empty:
        return pd.DataFrame(columns=columns)
    weekly = (
        valid.groupby("week", as_index=False)
        .agg(
            actual_km=("distance_km", "sum"),
            actual_sessions=("id", "count"),
            duration_min=("duration_min", "sum"),
            elevation_m=("elevation_m", lambda values: values.sum(min_count=1)),
            longest_run_km=("distance_km", "max"),
        )
        .sort_values("week")
    )
    weekly["pace_min_km"] = weekly["duration_min"] / weekly["actual_km"]
    weekly["volume_change_pct"] = weekly["actual_km"].pct_change() * 100
    return weekly.reset_index(drop=True)


def compare_plan_to_actual(plan, weekly):
    if plan.empty and weekly.empty:
        return pd.DataFrame()
    comparison = plan.merge(weekly, on="week", how="outer").sort_values("week")
    for column in [
        "actual_km",
        "actual_sessions",
        "duration_min",
        "longest_run_km",
    ]:
        comparison[column] = comparison[column].fillna(0)
    today = pd.Timestamp.today().normalize()
    current_week = today - pd.Timedelta(days=today.weekday())
    comparison["week_status"] = np.select(
        [
            comparison["week"] < current_week,
            comparison["week"] == current_week,
            comparison["week"] > current_week,
        ],
        ["concluída", "em andamento", "futura"],
        default="desconhecida",
    )
    completed_distance = (
        (comparison["week_status"] == "concluída")
        & comparison["planned_km"].notna()
        & (comparison["planned_km"] > 0)
    )
    comparison["distance_adherence_pct"] = np.where(
        completed_distance,
        100 * comparison["actual_km"] / comparison["planned_km"],
        np.nan,
    )
    completed_sessions = (
        (comparison["week_status"] == "concluída")
        & comparison["planned_sessions"].notna()
        & (comparison["planned_sessions"] > 0)
    )
    comparison["session_adherence_pct"] = np.where(
        completed_sessions,
        100 * comparison["actual_sessions"] / comparison["planned_sessions"],
        np.nan,
    )
    comparison["session_gap"] = np.where(
        comparison["planned_sessions"].notna(),
        comparison["actual_sessions"] - comparison["planned_sessions"],
        np.nan,
    )
    comparison["alignment_label"] = np.select(
        [
            comparison["week_status"] == "futura",
            comparison["week_status"] == "em andamento",
            comparison["planned_km"].isna(),
            comparison["distance_adherence_pct"] < 80,
            comparison["distance_adherence_pct"] <= 120,
            comparison["distance_adherence_pct"] > 120,
        ],
        [
            "semana futura",
            "semana em andamento",
            "sem plano cadastrado",
            "abaixo do plano",
            "próximo do plano",
            "acima do plano",
        ],
        default="sem avaliação",
    )
    return comparison.reset_index(drop=True)


def format_pace(pace):
    if pd.isna(pace) or pace <= 0:
        return "Não disponível"
    minutes, seconds = divmod(round(float(pace) * 60), 60)
    return f"{minutes}:{seconds:02d} min/km"


COLORS = {
    "primary": "#FC4C02",
    "secondary": "#172A3A",
    "accent": "#2A9D8F",
    "muted": "#94A3B8",
}


def empty_chart(message, title):
    figure = go.Figure()
    figure.add_annotation(
        text=message,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
    )
    figure.update_layout(
        title=title,
        template="plotly_white",
        height=400,
        xaxis={"visible": False},
        yaxis={"visible": False},
    )
    return figure


def chart_plan_vs_actual(comparison):
    title = "Volume semanal: planejado x realizado"
    if comparison.empty:
        return empty_chart("Cadastre um plano e carregue treinos.", title)
    figure = go.Figure()
    figure.add_bar(
        x=comparison["week"],
        y=comparison["actual_km"],
        name="Realizado",
        marker_color=COLORS["primary"],
    )
    figure.add_scatter(
        x=comparison["week"],
        y=comparison["planned_km"],
        name="Planejado",
        mode="lines+markers",
        line={"color": COLORS["secondary"], "width": 3},
    )
    figure.update_layout(
        title=title,
        xaxis_title="Semana",
        yaxis_title="Distância (km)",
        template="plotly_white",
        hovermode="x unified",
        height=430,
    )
    return figure


def chart_pace_trend(runs):
    title = "Ritmo das atividades ao longo do tempo"
    if runs.empty:
        return empty_chart("Carregue treinos para visualizar o ritmo.", title)
    data = runs.dropna(subset=["date", "pace_min_km", "distance_km"]).copy()
    data = data[(data["pace_min_km"] > 0) & (data["distance_km"] > 0)]
    if data.empty:
        return empty_chart("Não há atividades com ritmo válido.", title)
    figure = px.scatter(
        data,
        x="date",
        y="pace_min_km",
        size="distance_km",
        color="data_source",
        hover_name="name",
        hover_data={"distance_km": ":.2f", "average_hr": ":.0f"},
        labels={"date": "Data", "pace_min_km": "Ritmo (min/km)"},
        color_discrete_map={"demo": COLORS["accent"], "strava": COLORS["primary"]},
        title=title,
        template="plotly_white",
        size_max=24,
    )
    figure.update_yaxes(autorange="reversed")
    figure.update_layout(height=430)
    return figure


def chart_weekly_progress(weekly):
    title = "Volume semanal e treino mais longo"
    if weekly.empty:
        return empty_chart("Não há métricas semanais.", title)
    figure = go.Figure()
    figure.add_bar(
        x=weekly["week"],
        y=weekly["actual_km"],
        name="Volume semanal",
        marker_color=COLORS["primary"],
    )
    figure.add_scatter(
        x=weekly["week"],
        y=weekly["longest_run_km"],
        name="Treino mais longo",
        mode="lines+markers",
        line={"color": COLORS["secondary"], "width": 3},
    )
    figure.update_layout(
        title=title,
        xaxis_title="Semana",
        yaxis_title="Distância (km)",
        template="plotly_white",
        hovermode="x unified",
        height=430,
    )
    return figure


KNOWLEDGE_DOCUMENTS = [
    {
        "title": "Separação entre evidência e recomendação",
        "source": "Política metodológica RunLens",
        "source_url": "",
        "content": (
            "A análise começa pelos valores observados. Inferências são hipóteses, "
            "recomendações identificam as métricas usadas e valores ausentes não são zero."
        ),
    },
    {
        "title": "Monitoramento individual da carga de treinamento",
        "source": "IOC Consensus Statement on Load in Sport",
        "source_url": "https://bjsm.bmj.com/content/50/17/1030",
        "content": (
            "A carga deve ser interpretada individualmente e com múltiplos indicadores. "
            "Volume, intensidade, recuperação, bem-estar e histórico interagem. Uma única "
            "métrica ou semana não basta para concluir prontidão, fadiga ou risco de lesão."
        ),
    },
    {
        "title": "Atividade física e contexto de saúde",
        "source": "World Health Organization",
        "source_url": "https://www.who.int/publications/b/55501",
        "content": (
            "As diretrizes da OMS são recomendações populacionais e servem como contexto "
            "geral, não como prescrição individual de prova ou tratamento médico."
        ),
    },
    {
        "title": "Limitações dos dados obtidos no Strava",
        "source": "Strava API Reference",
        "source_url": "https://developers.strava.com/docs/reference/",
        "content": (
            "Campos dependem das permissões, privacidade e dispositivo. Frequência cardíaca "
            "e elevação podem não existir; campo ausente não representa valor zero."
        ),
    },
    {
        "title": "Limites de segurança do RunLens",
        "source": "Política de segurança RunLens",
        "source_url": "",
        "content": (
            "O sistema é educacional, não diagnostica lesões ou doenças. Dor, tontura, "
            "mal-estar, falta de ar incomum ou piora persistente requerem avaliação profissional."
        ),
    },
]


def initialize_knowledge_base():
    with get_db_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (title, source)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                title, content, source, source_url UNINDEXED,
                content='knowledge_documents', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER IF NOT EXISTS knowledge_documents_ai AFTER INSERT ON knowledge_documents BEGIN
                INSERT INTO knowledge_fts(rowid, title, content, source, source_url)
                VALUES (new.id, new.title, new.content, new.source, new.source_url);
            END;
            CREATE TRIGGER IF NOT EXISTS knowledge_documents_ad AFTER DELETE ON knowledge_documents BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, source, source_url)
                VALUES ('delete', old.id, old.title, old.content, old.source, old.source_url);
            END;
            CREATE TRIGGER IF NOT EXISTS knowledge_documents_au AFTER UPDATE ON knowledge_documents BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, title, content, source, source_url)
                VALUES ('delete', old.id, old.title, old.content, old.source, old.source_url);
                INSERT INTO knowledge_fts(rowid, title, content, source, source_url)
                VALUES (new.id, new.title, new.content, new.source, new.source_url);
            END;
            """
        )
        for document in KNOWLEDGE_DOCUMENTS:
            connection.execute(
                """
                INSERT INTO knowledge_documents (title, content, source, source_url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (title, source) DO UPDATE SET
                    content = excluded.content,
                    source_url = excluded.source_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    document["title"],
                    document["content"],
                    document["source"],
                    document["source_url"],
                ),
            )
        connection.execute(
            "INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')"
        )


STOPWORDS = {
    "como",
    "para",
    "com",
    "sem",
    "uma",
    "que",
    "dos",
    "das",
    "por",
    "sobre",
    "meu",
    "minha",
    "nos",
    "nas",
    "este",
    "esta",
    "isso",
    "mais",
}


def search_knowledge(query, top_k=3):
    terms = re.findall(r"[A-Za-zÀ-ÿ0-9]+", (query or "").lower())
    terms = [term for term in terms if len(term) >= 3 and term not in STOPWORDS]
    terms = list(dict.fromkeys(terms))[:12]
    if not terms:
        return []
    fts_query = " OR ".join(f'"{term}"' for term in terms)
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT documents.title, documents.content, documents.source,
                   documents.source_url, bm25(knowledge_fts) AS score
            FROM knowledge_fts
            INNER JOIN knowledge_documents AS documents
                ON documents.id = knowledge_fts.rowid
            WHERE knowledge_fts MATCH ? ORDER BY score LIMIT ?
            """,
            (fts_query, max(1, min(int(top_k), 5))),
        ).fetchall()
    return [dict(row) for row in rows]


def dataframe_records(frame):
    if frame is None or frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def to_json_payload(payload):
    def converter(value):
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return value.isoformat()
        raise TypeError(f"Tipo não serializável: {type(value).__name__}")

    return json.dumps(
        payload,
        ensure_ascii=False,
        default=converter,
        allow_nan=False,
        indent=2,
    )


def build_runlens_tools(athlete_id):
    @tool("consultar_objetivo_atleta")
    def get_goal() -> str:
        """Retorna objetivo, prova, tempo e ritmo-alvo do atleta."""
        goal = load_athlete_goal(athlete_id)
        return to_json_payload(goal or {"status": "sem_objetivo"})

    @tool("consultar_qualidade_dados")
    def get_quality() -> str:
        """Retorna origem, período e cobertura dos dados."""
        return to_json_payload(
            calculate_data_quality(load_athlete_activities(athlete_id))
        )

    @tool("consultar_resumo_semanal")
    def get_weekly(limit_weeks: int = 8) -> str:
        """Retorna volume, sessões, duração, treino longo e ritmo por semana."""
        weekly = calculate_weekly_metrics(load_athlete_activities(athlete_id))
        return to_json_payload(
            {"weeks": dataframe_records(weekly.tail(max(1, min(limit_weeks, 24))))}
        )

    @tool("comparar_plano_realizado")
    def compare_plan(limit_weeks: int = 8) -> str:
        """Compara o plano cadastrado aos treinos realizados."""
        plan = load_weekly_plan(athlete_id)
        if plan.empty:
            return to_json_payload({"status": "sem_plano", "weeks": []})
        weekly = calculate_weekly_metrics(load_athlete_activities(athlete_id))
        comparison = compare_plan_to_actual(plan, weekly)
        return to_json_payload(
            {
                "status": "ok",
                "weeks": dataframe_records(
                    comparison.tail(max(1, min(limit_weeks, 24)))
                ),
            }
        )

    @tool("consultar_corridas_recentes")
    def get_recent(limit: int = 5) -> str:
        """Retorna as corridas mais recentes e suas métricas disponíveis."""
        runs = load_athlete_activities(athlete_id).tail(max(1, min(limit, 30)))
        columns = [
            "date",
            "name",
            "data_source",
            "distance_km",
            "duration_min",
            "pace_min_km",
            "average_hr",
            "elevation_m",
        ]
        return to_json_payload({"runs": dataframe_records(runs[columns])})

    @tool("pesquisar_base_conhecimento")
    def search_base(query: str, top_k: int = 3) -> str:
        """Pesquisa metodologia, limitações e segurança na base local."""
        return to_json_payload(
            {"documents": search_knowledge(query, max(1, min(top_k, 5)))}
        )

    return [get_goal, get_quality, get_weekly, compare_plan, get_recent, search_base]


BACKSTORY = """
Você é o RunLens Coach, especialista em augmented analytics para corrida.
Transforma objetivos, planos e treinos em análises claras e auditáveis.
Usa evidências recuperadas pelas ferramentas, declara limitações e não inventa dados.
Não é médico, não diagnostica lesões e não prescreve tratamento.
Responde em português do Brasil de forma objetiva e acolhedora.
""".strip()


RULES = """
Consulte objetivo e qualidade antes de concluir. Use ferramentas para toda afirmação
sobre o atleta. Separe evidência, interpretação e próximo passo. Cite datas e métricas.
Não trate dado ausente como zero nem ritmo-alvo como ritmo de todas as sessões.
Só avalie aderência se houver plano. Use a Knowledge Base para metodologia e fontes.
Não faça diagnóstico ou tratamento médico.
""".strip()


def ask_runlens(athlete_id, question):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")
    if not question.strip():
        raise ValueError("Digite uma pergunta.")
    if load_athlete_goal(athlete_id) is None:
        raise ValueError("Cadastre um objetivo antes de consultar o agente.")
    agent = Agent(
        role="RunLens Coach — Especialista em Analytics de Corrida",
        goal=(
            "Produzir insights claros e rastreáveis usando somente dados consultados "
            "pelas ferramentas disponíveis."
        ),
        backstory=BACKSTORY,
        tools=build_runlens_tools(athlete_id),
        llm=CREWAI_MODEL,
        verbose=False,
        allow_delegation=False,
        max_iter=8,
    )
    task = Task(
        description=f"""
        Analise a pergunta abaixo usando as ferramentas disponíveis.

        {RULES}

        <pergunta_do_usuario>
        {question.strip()[:4000]}
        </pergunta_do_usuario>
        """,
        expected_output=(
            "Resposta em Markdown com as seções: Resumo, Evidências observadas, "
            "Interpretação, Próximo passo sugerido, Limitações e fontes."
        ),
        agent=agent,
    )
    result = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        memory=False,
    ).kickoff()
    return getattr(result, "raw", None) or str(result)


def parse_agent_json(raw_text):
    """Extrai e repara um objeto JSON devolvido pelo agente."""
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("O agente não devolveu um plano em JSON.")
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = repair_json(candidate, return_objects=True)
        if not isinstance(repaired, dict):
            raise ValueError("Não foi possível interpretar o plano gerado.")
        return repaired


def generate_training_plan(athlete_id, requested_weeks=8):
    """Gera uma proposta semanal; a gravação depende de aprovação humana."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")

    goal = load_athlete_goal(athlete_id)
    if goal is None:
        raise ValueError("Cadastre um objetivo antes de gerar o plano.")

    requested_weeks = max(2, min(int(requested_weeks), 16))
    today = pd.Timestamp.today().normalize()
    next_monday = today - pd.Timedelta(days=today.weekday()) + pd.Timedelta(days=7)
    race_date = pd.Timestamp(goal["race_date"])
    week_dates = []
    cursor = next_monday
    while len(week_dates) < requested_weeks and cursor.date() <= race_date.date():
        week_dates.append(cursor.date().isoformat())
        cursor += pd.Timedelta(days=7)

    if len(week_dates) < 2:
        raise ValueError(
            "Há menos de duas semanas completas até a prova. "
            "O protótipo não deve gerar automaticamente um plano tão próximo do evento."
        )

    tools = build_runlens_tools(athlete_id)
    planner = Agent(
        role="RunLens Planner — Especialista em Planejamento de Corrida",
        goal=(
            "Propor um plano semanal progressivo, auditável e compatível com o objetivo, "
            "a disponibilidade e o histórico efetivamente recuperado pelas ferramentas."
        ),
        backstory=(
            BACKSTORY
            + "\nVocê cria propostas de planejamento, mas nunca as grava diretamente. "
            "O atleta precisa revisar e aprovar cada semana."
        ),
        tools=tools,
        llm=CREWAI_MODEL,
        verbose=False,
        allow_delegation=False,
        max_iter=10,
    )

    task = Task(
        description=f"""
        Crie uma proposta de plano semanal para o atleta ativo.

        Antes de planejar, use obrigatoriamente as ferramentas para consultar:
        1. objetivo e disponibilidade;
        2. qualidade dos dados;
        3. resumo semanal e corridas recentes;
        4. base de conhecimento sobre carga, limitações e segurança.

        Regras:
        - use somente as semanas listadas abaixo, na mesma ordem;
        - gere exatamente {len(week_dates)} semanas;
        - nunca ultrapasse {goal['available_days_per_week']} sessões por semana;
        - baseie o volume no histórico recuperado; se ele não existir, declare a limitação;
        - não diagnostique, não prometa resultado e não invente métricas;
        - descreva em notes uma distribuição geral das sessões, sem tratamento médico;
        - não use Markdown na resposta final: devolva somente JSON válido.

        Semanas permitidas:
        {json.dumps(week_dates, ensure_ascii=False)}

        Formato obrigatório:
        {{
          "summary": "resumo da lógica do plano",
          "assumptions": ["premissa 1"],
          "limitations": ["limitação 1"],
          "weeks": [
            {{
              "week": "YYYY-MM-DD",
              "planned_km": 0.0,
              "planned_sessions": 0,
              "notes": "descrição breve da semana"
            }}
          ]
        }}
        """.strip(),
        expected_output="Um único objeto JSON válido, sem texto antes ou depois.",
        agent=planner,
    )

    result = Crew(
        agents=[planner],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        memory=False,
    ).kickoff()
    raw = getattr(result, "raw", None) or str(result)
    payload = parse_agent_json(raw)

    weeks = payload.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        raise ValueError("O agente não devolveu semanas válidas.")

    plan = normalize_weekly_plan(pd.DataFrame(weeks))
    expected_dates = set(week_dates)
    returned_dates = set(plan["week"].dt.date.astype(str).tolist())
    if returned_dates != expected_dates:
        raise ValueError(
            "As semanas devolvidas pelo agente não correspondem ao período solicitado. "
            "Tente gerar novamente."
        )
    if len(plan) != len(week_dates):
        raise ValueError("O agente devolveu uma quantidade incorreta de semanas.")
    if (plan["planned_sessions"] > goal["available_days_per_week"]).any():
        raise ValueError("O agente excedeu a disponibilidade semanal do atleta.")

    return {
        "summary": str(payload.get("summary", "")),
        "assumptions": payload.get("assumptions", []),
        "limitations": payload.get("limitations", []),
        "plan": plan,
    }


initialize_database()
initialize_knowledge_base()

st.title("🏃 RunLens AI")
st.caption("Augmented analytics para planejamento e análise de treinos de corrida")

athletes = list_athletes()
athlete_map = {item["id"]: item["athlete_name"] for item in athletes}
active_id = st.session_state.get("active_athlete_id")

with st.sidebar:
    st.header("Atleta ativo")
    if athletes:
        ids = list(athlete_map)
        default_index = ids.index(active_id) if active_id in ids else 0
        active_id = st.selectbox(
            "Selecione o perfil",
            options=ids,
            index=default_index,
            format_func=lambda value: athlete_map[value],
        )
        st.session_state["active_athlete_id"] = active_id
    else:
        st.info("Cadastre o primeiro objetivo.")
        active_id = None

    st.divider()
    st.caption(f"Gemini: {GEMINI_MODEL_ID}")
    st.caption("Banco: SQLite no Google Drive")

tabs = st.tabs(
    ["🎯 Objetivo", "🏃 Treinos", "📅 Plano", "📊 Analytics", "🤖 Agente"]
)

with tabs[0]:
    st.subheader("Objetivo da prova")
    current_goal = load_athlete_goal(active_id)
    default_date = datetime.now(timezone.utc).date() + timedelta(days=120)
    if current_goal:
        default_date = datetime.strptime(current_goal["race_date"], "%Y-%m-%d").date()

    with st.form("goal_form"):
        col1, col2 = st.columns(2)
        with col1:
            athlete_name = st.text_input(
                "Nome do atleta",
                value=current_goal["athlete_name"] if current_goal else "",
            )
            race_name = st.text_input(
                "Nome da prova",
                value=current_goal["race_name"] if current_goal else "",
            )
            race_date = st.date_input("Data da prova", value=default_date)
        with col2:
            race_distance = st.number_input(
                "Distância da prova (km)",
                min_value=0.1,
                value=float(current_goal["race_distance_km"]) if current_goal else 10.0,
                step=0.5,
            )
            target_time = st.number_input(
                "Tempo-alvo (minutos)",
                min_value=1.0,
                value=float(current_goal["target_time_minutes"]) if current_goal else 50.0,
                step=1.0,
            )
            available_days = st.number_input(
                "Dias disponíveis por semana",
                min_value=1,
                max_value=7,
                value=int(current_goal["available_days_per_week"]) if current_goal else 4,
            )
        notes = st.text_area(
            "Observações",
            value=current_goal.get("notes", "") if current_goal else "",
        )
        submitted_goal = st.form_submit_button(
            "Salvar objetivo", type="primary", use_container_width=True
        )

    if submitted_goal:
        try:
            athlete_id = save_goal(
                athlete_name,
                race_name,
                race_distance,
                race_date,
                target_time,
                available_days,
                notes,
            )
            st.session_state["active_athlete_id"] = athlete_id
            st.success("Objetivo salvo no SQLite.")
            st.rerun()
        except Exception as error:
            st.error(str(error))

    if current_goal:
        col1, col2, col3 = st.columns(3)
        col1.metric("Ritmo-alvo", current_goal["target_pace_label"])
        col2.metric("Dias até a prova", current_goal["days_to_race"])
        col3.metric("Semanas até a prova", current_goal["weeks_to_race"])

with tabs[1]:
    st.subheader("Fonte dos treinos")
    if not active_id:
        st.warning("Cadastre e selecione um atleta primeiro.")
    else:
        source = st.radio(
            "Escolha a fonte",
            ["Demonstração", "Strava"],
            horizontal=True,
        )
        if source == "Demonstração":
            demo_weeks = st.slider("Semanas fictícias", 4, 24, 12)
            st.caption("Treinos sintéticos reproduzíveis; o objetivo continua sendo real.")
            token = ""
            lookback = 90
        else:
            lookback = st.slider("Período do Strava (dias)", 30, 365, 90)
            if STRAVA_ACCESS_TOKEN:
                st.success("Token do Strava configurado no ambiente.")
                token_override = st.text_input(
                    "Token alternativo (opcional)",
                    type="password",
                    help="Usado apenas nesta sessão e não salvo no banco.",
                )
                token = token_override or STRAVA_ACCESS_TOKEN
            else:
                token = st.text_input(
                    "Token de acesso do Strava",
                    type="password",
                    help="Usado apenas nesta sessão e não salvo no banco.",
                )
            demo_weeks = 12

        if st.button("Carregar e salvar treinos", type="primary"):
            try:
                with st.spinner("Carregando atividades..."):
                    if source == "Demonstração":
                        activities = make_demo_activities(demo_weeks)
                        saved = save_activities(
                            active_id, activities, "demo", replace_source=True
                        )
                    else:
                        activities = load_strava_activities(token, lookback)
                        saved = save_activities(active_id, activities, "strava")
                st.success(f"{saved} atividades processadas.")
            except Exception as error:
                st.error(f"Não foi possível carregar os treinos: {error}")

        runs = load_athlete_activities(active_id)
        quality = calculate_data_quality(runs)
        col1, col2, col3 = st.columns(3)
        col1.metric("Atividades", quality["runs_received"])
        col2.metric("Treinos válidos", quality["valid_runs"])
        col3.metric("Cobertura de FC", f"{quality['heart_rate_coverage_pct']:.1f}%")
        if not runs.empty:
            recent = runs.tail(10).copy()
            recent["Ritmo"] = recent["pace_min_km"].apply(format_pace)
            st.dataframe(
                recent[["date", "name", "data_source", "distance_km", "duration_min", "Ritmo"]],
                use_container_width=True,
                hide_index=True,
            )

with tabs[2]:
    st.subheader("Plano semanal gerado pelo RunLens Planner")
    st.caption(
        "O agente propõe o plano com base no objetivo e no histórico. "
        "Nada é salvo antes da sua revisão e aprovação."
    )
    if not active_id:
        st.warning("Cadastre e selecione um atleta primeiro.")
    else:
        goal = load_athlete_goal(active_id)
        runs_for_plan = load_athlete_activities(active_id)
        existing_plan = load_weekly_plan(active_id)

        if runs_for_plan.empty:
            st.warning(
                "Ainda não existem treinos carregados. O agente poderá criar uma "
                "proposta, mas declarará que não há histórico para individualizar o volume."
            )

        horizon = st.slider(
            "Horizonte da proposta (semanas)",
            min_value=2,
            max_value=16,
            value=8,
        )

        if st.button("Gerar plano com IA", type="primary"):
            try:
                with st.spinner(
                    "O agente está analisando objetivo, treinos e Knowledge Base..."
                ):
                    proposal = generate_training_plan(active_id, horizon)
                st.session_state[f"generated_plan_{active_id}"] = proposal["plan"]
                st.session_state[f"plan_summary_{active_id}"] = proposal["summary"]
                st.session_state[f"plan_assumptions_{active_id}"] = proposal[
                    "assumptions"
                ]
                st.session_state[f"plan_limitations_{active_id}"] = proposal[
                    "limitations"
                ]
                version_key = f"plan_editor_version_{active_id}"
                st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
                st.success("Proposta gerada. Revise antes de aprovar.")
                st.rerun()
            except Exception as error:
                st.error(f"Não foi possível gerar o plano: {error}")

        generated_plan = st.session_state.get(f"generated_plan_{active_id}")
        if generated_plan is not None:
            editor_data = generated_plan.copy()
            summary = st.session_state.get(f"plan_summary_{active_id}")
            assumptions = st.session_state.get(f"plan_assumptions_{active_id}", [])
            limitations = st.session_state.get(f"plan_limitations_{active_id}", [])
            if summary:
                st.info(summary)
            if assumptions or limitations:
                with st.expander("Premissas e limitações da proposta"):
                    if assumptions:
                        st.markdown("**Premissas**")
                        for item in assumptions:
                            st.write(f"- {item}")
                    if limitations:
                        st.markdown("**Limitações**")
                        for item in limitations:
                            st.write(f"- {item}")
        elif not existing_plan.empty:
            editor_data = existing_plan.copy()
            st.info("Exibindo o plano atualmente aprovado e salvo no banco.")
        else:
            editor_data = pd.DataFrame()
            st.info("Clique em “Gerar plano com IA” para criar a primeira proposta.")

        if not editor_data.empty:
            editor_version = st.session_state.get(
                f"plan_editor_version_{active_id}", 0
            )
            edited_plan = st.data_editor(
                editor_data,
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "week": st.column_config.DateColumn("Semana"),
                    "planned_km": st.column_config.NumberColumn(
                        "Km planejados", min_value=0.0, format="%.1f"
                    ),
                    "planned_sessions": st.column_config.NumberColumn(
                        "Sessões",
                        min_value=0,
                        max_value=int(goal["available_days_per_week"]),
                        step=1,
                    ),
                    "notes": st.column_config.TextColumn("Estrutura da semana"),
                },
                key=f"plan_editor_{active_id}_{editor_version}",
            )
            st.warning(
                "Revise volume, número de sessões e observações. "
                "A aprovação é uma decisão humana."
            )
            if st.button("Aprovar e salvar plano", type="primary"):
                try:
                    if (
                        pd.to_numeric(
                            edited_plan["planned_sessions"], errors="coerce"
                        )
                        > int(goal["available_days_per_week"])
                    ).any():
                        raise ValueError(
                            "O plano excede os dias disponíveis do atleta."
                        )
                    count = save_weekly_plan(
                        active_id, edited_plan, replace_existing=True
                    )
                    st.session_state.pop(f"generated_plan_{active_id}", None)
                    st.success(f"Plano aprovado e salvo: {count} semanas.")
                    st.rerun()
                except Exception as error:
                    st.error(str(error))

        with st.expander("Alternativa manual: importar CSV"):
            uploaded = st.file_uploader(
                "CSV com week, planned_km, planned_sessions e notes",
                type=["csv"],
                key=f"plan_upload_{active_id}",
            )
            if uploaded is not None and st.button("Usar plano do CSV"):
                try:
                    imported = normalize_weekly_plan(pd.read_csv(uploaded))
                    st.session_state[f"generated_plan_{active_id}"] = imported
                    st.session_state[f"plan_summary_{active_id}"] = (
                        "Plano importado manualmente por CSV."
                    )
                    version_key = f"plan_editor_version_{active_id}"
                    st.session_state[version_key] = (
                        st.session_state.get(version_key, 0) + 1
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"CSV inválido: {error}")

with tabs[3]:
    st.subheader("Dashboard analítico")
    if not active_id:
        st.warning("Cadastre e selecione um atleta primeiro.")
    else:
        goal = load_athlete_goal(active_id)
        runs = load_athlete_activities(active_id)
        weekly = calculate_weekly_metrics(runs)
        plan = load_weekly_plan(active_id)
        comparison = compare_plan_to_actual(plan, weekly)
        quality = calculate_data_quality(runs)

        total_km = runs.loc[runs["is_valid_activity"], "distance_km"].sum()
        longest = runs["distance_km"].max() if not runs.empty else 0
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Volume total", f"{total_km:.1f} km")
        col2.metric("Treinos", quality["valid_runs"])
        col3.metric("Treino mais longo", f"{longest:.1f} km")
        col4.metric("Ritmo-alvo", goal["target_pace_label"] if goal else "—")

        left, right = st.columns(2)
        with left:
            st.plotly_chart(chart_weekly_progress(weekly), use_container_width=True)
        with right:
            st.plotly_chart(chart_pace_trend(runs), use_container_width=True)
        st.plotly_chart(chart_plan_vs_actual(comparison), use_container_width=True)

        with st.expander("Qualidade e cobertura dos dados"):
            st.json(quality)
        if not comparison.empty:
            st.dataframe(comparison.tail(12), use_container_width=True, hide_index=True)

with tabs[4]:
    st.subheader("RunLens Coach")
    st.caption("Agente CrewAI com persona especialista e Knowledge Base local.")
    if not active_id:
        st.warning("Cadastre e selecione um atleta primeiro.")
    elif not GEMINI_API_KEY:
        st.error("GEMINI_API_KEY não foi disponibilizada ao Streamlit.")
    else:
        question = st.text_area(
            "Pergunta para o agente",
            placeholder="Ex.: Estou aderindo ao plano nas últimas semanas? Mostre as evidências.",
            height=120,
        )
        if st.button("Analisar com IA", type="primary"):
            try:
                with st.spinner("O agente está consultando os dados e a Knowledge Base..."):
                    st.session_state[f"agent_answer_{active_id}"] = ask_runlens(
                        active_id, question
                    )
            except Exception as error:
                st.error(f"Falha ao executar o agente: {error}")
        if st.session_state.get(f"agent_answer_{active_id}"):
            st.markdown(st.session_state[f"agent_answer_{active_id}"])

st.divider()
st.caption(
    "RunLens AI é um protótipo acadêmico e educacional. Não substitui treinador, "
    "médico ou outro profissional qualificado."
)
