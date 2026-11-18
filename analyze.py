from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_AS_OF = "2026-06-17T07:45:00+05:30"
EXCLUDED_OPERATORS = {7}
POLICY_ON_TIME_ROUTES = {12}
MAX_CLOCK_SKEW_SECONDS = 120
MAX_CROSS_TRACK_METERS = 500
MAX_SPEED_KMPH = 120
FRESHNESS_MINUTES = 5
STALL_WINDOW_MINUTES = 20
LATE_THRESHOLD_MINUTES = 5


@dataclass
class Inputs:
    routes: pd.DataFrame
    stops: pd.DataFrame
    trips: pd.DataFrame
    bookings: pd.DataFrame
    pings: pd.DataFrame


def parse_timestamps(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, format="mixed", errors="coerce", utc=True)


def load_inputs(data_dir: Path) -> Inputs:
    routes = pd.read_csv(data_dir / "routes.csv")
    stops = pd.read_csv(data_dir / "stops.csv")
    trips = pd.read_csv(data_dir / "trips.csv")
    bookings = pd.read_csv(data_dir / "bookings.csv")
    pings = pd.read_csv(data_dir / "gps_pings.csv")

    trips["service_date"] = trips["service_date"].astype(str)
    for column in ("scheduled_start", "scheduled_end"):
        trips[column] = parse_timestamps(trips[column])
    for column in ("recorded_at", "received_at"):
        pings[column] = parse_timestamps(pings[column])
    pings["service_date"] = (
        pings["received_at"]
        .dt.tz_convert("Asia/Kolkata")
        .dt.strftime("%Y-%m-%d")
    )
    pings["clock_skew_seconds"] = (
        pings["received_at"] - pings["recorded_at"]
    ).dt.total_seconds()

    return Inputs(routes, stops, trips, bookings, pings)


def project_to_route(
    latitude: float, longitude: float, route_stops: pd.DataFrame
) -> tuple[float, float]:
    ordered = route_stops.sort_values("seq")
    reference_latitude = np.radians(ordered["lat"].mean())
    meters_per_degree_x = 111_320 * np.cos(reference_latitude)
    route_x = ordered["lon"].to_numpy(dtype=float) * meters_per_degree_x
    route_y = ordered["lat"].to_numpy(dtype=float) * 111_320
    point_x = longitude * meters_per_degree_x
    point_y = latitude * 111_320

    total_length = 0.0
    best_distance = float("inf")
    best_along = 0.0

    for index in range(len(ordered) - 1):
        start = np.array([route_x[index], route_y[index]])
        end = np.array([route_x[index + 1], route_y[index + 1]])
        segment = end - start
        segment_length = float(np.linalg.norm(segment))
        if segment_length == 0:
            continue

        point = np.array([point_x, point_y])
        fraction = float(
            np.clip(np.dot(point - start, segment) / segment_length**2, 0, 1)
        )
        projected = start + fraction * segment
        distance = float(np.linalg.norm(point - projected))
        if distance < best_distance:
            best_distance = distance
            best_along = total_length + fraction * segment_length
        total_length += segment_length

    if total_length == 0 or best_distance == float("inf"):
        raise ValueError("A route must have at least two distinct stops.")
    return best_distance, best_along / total_length


def match_and_project(inputs: Inputs) -> pd.DataFrame:
    candidates = inputs.pings.merge(
        inputs.trips,
        on=["vehicle_id", "service_date"],
        suffixes=("", "_trip"),
    )
    candidates = candidates[
        candidates["recorded_at"].notna()
        & candidates["scheduled_start"].notna()
        & candidates["scheduled_end"].notna()
        & (candidates["recorded_at"] >= candidates["scheduled_start"] - pd.Timedelta(minutes=10))
        & (candidates["recorded_at"] <= candidates["scheduled_end"] + pd.Timedelta(minutes=30))
    ].copy()

    projections: list[tuple[float, float]] = []
    for row in candidates.itertuples():
        route_stops = inputs.stops[inputs.stops["route_id"] == row.route_id]
        projections.append(project_to_route(row.lat, row.lon, route_stops))
    if projections:
        candidates[["cross_track_m", "progress"]] = projections
    else:
        candidates["cross_track_m"] = pd.Series(dtype=float)
        candidates["progress"] = pd.Series(dtype=float)

    candidates["valid_evidence"] = (
        candidates["recorded_at"].notna()
        & candidates["received_at"].notna()
        & candidates["clock_skew_seconds"].between(0, MAX_CLOCK_SKEW_SECONDS)
        & (candidates["cross_track_m"] <= MAX_CROSS_TRACK_METERS)
        & candidates["speed_kmph"].between(0, MAX_SPEED_KMPH)
    )
    return candidates


def historical_lag_minutes(
    trip: pd.Series,
    current_progress: float,
    as_of: pd.Timestamp,
    trips: pd.DataFrame,
    projected: pd.DataFrame,
) -> tuple[float | None, list[float]]:
    current_elapsed = (as_of - trip["scheduled_start"]).total_seconds() / 60
    elapsed_at_progress: list[float] = []
    prior_trips = trips[
        (trips["route_id"] == trip["route_id"])
        & (trips["service_date"] < trip["service_date"])
    ]

    for historical_trip in prior_trips.itertuples():
        history = projected[
            (projected["trip_id"] == historical_trip.trip_id)
            & projected["valid_evidence"]
            & ~projected["operator_id"].isin(EXCLUDED_OPERATORS)
        ].sort_values("recorded_at")
        history = history[
            history["recorded_at"] >= historical_trip.scheduled_start
        ].copy()
        if len(history) < 2:
            continue

        history["elapsed_min"] = (
            history["recorded_at"] - historical_trip.scheduled_start
        ).dt.total_seconds() / 60
        progress = np.maximum.accumulate(history["progress"].to_numpy(dtype=float))
        if current_progress > progress[-1]:
            continue

        keep = np.r_[True, np.diff(progress) > 1e-8]
        unique_progress = progress[keep]
        unique_elapsed = history["elapsed_min"].to_numpy(dtype=float)[keep]
        if len(unique_progress) >= 2:
            elapsed_at_progress.append(
                float(np.interp(current_progress, unique_progress, unique_elapsed))
            )

    if not elapsed_at_progress:
        return None, []
    return current_elapsed - float(np.median(elapsed_at_progress)), elapsed_at_progress


def decide(
    route_id: int,
    age_minutes: float | None,
    lag_minutes: float | None,
    stalled: bool,
    slow_final_pings: bool,
) -> str:
    if route_id in POLICY_ON_TIME_ROUTES:
        return "HOLD"
    if age_minutes is None:
        return "NO_VERDICT"
    if stalled or (age_minutes > FRESHNESS_MINUTES and slow_final_pings):
        return "CALL_DRIVER"
    if age_minutes > FRESHNESS_MINUTES or lag_minutes is None:
        return "NO_VERDICT"
    if lag_minutes >= LATE_THRESHOLD_MINUTES:
        return f"PUSH_LATE {int(np.floor(lag_minutes))} min"
    return "HOLD"


def _trip_operator_ids(projected: pd.DataFrame, trip_id: str) -> set[int]:
    values = projected.loc[projected["trip_id"] == trip_id, "operator_id"]
    return set(values.dropna().astype(int).tolist())


def analyze(data_dir: Path, as_of_text: str = DEFAULT_AS_OF) -> tuple[pd.DataFrame, dict[str, int]]:
    inputs = load_inputs(data_dir)
    projected = match_and_project(inputs)
    as_of = pd.Timestamp(as_of_text).tz_convert("UTC")
    live_date = as_of.tz_convert("Asia/Kolkata").strftime("%Y-%m-%d")
    live_trips = inputs.trips[inputs.trips["service_date"] == live_date]
    rows: list[dict[str, Any]] = []

    for _, trip in live_trips.iterrows():
        all_trip_pings = projected[projected["trip_id"] == trip["trip_id"]]
        operators = _trip_operator_ids(projected, trip["trip_id"])
        if operators & EXCLUDED_OPERATORS:
            continue

        evidence = all_trip_pings[
            all_trip_pings["valid_evidence"]
            & (all_trip_pings["received_at"] <= as_of)
        ].sort_values("received_at")
        route = inputs.routes[inputs.routes["route_id"] == trip["route_id"]].iloc[0]
        booking_count = int((inputs.bookings["trip_id"] == trip["trip_id"]).sum())

        if evidence.empty:
            rows.append(
                {
                    "route_id": int(trip["route_id"]),
                    "route_name": route["route_name"],
                    "trip_id": trip["trip_id"],
                    "bookings": booking_count,
                    "verdict": decide(int(trip["route_id"]), None, None, False, False),
                    "note": "No timestamp-, clock-, and geometry-valid ping is available by the as-of time.",
                    "age_minutes": None,
                    "progress": None,
                    "lag_minutes": None,
                    "ping_id": None,
                }
            )
            continue

        latest = evidence.iloc[-1]
        age_minutes = (as_of - latest["received_at"]).total_seconds() / 60
        lag_minutes, historical_elapsed = historical_lag_minutes(
            trip, float(latest["progress"]), as_of, inputs.trips, projected
        )
        recent = evidence[
            evidence["received_at"] >= as_of - pd.Timedelta(minutes=STALL_WINDOW_MINUTES)
        ]
        progress_span = (
            float(recent["progress"].max() - recent["progress"].min())
            if not recent.empty
            else 1.0
        )
        stalled = (
            (as_of - trip["scheduled_start"]).total_seconds() >= 10 * 60
            and len(recent) >= 3
            and progress_span <= 0.01
            and float(recent["speed_kmph"].max()) <= 5
        )
        slow_final_pings = (
            len(evidence) >= 3
            and bool(evidence.tail(3)["speed_kmph"].le(5).all())
        )
        verdict = decide(
            int(trip["route_id"]),
            age_minutes,
            lag_minutes,
            stalled,
            slow_final_pings,
        )

        if int(trip["route_id"]) == 12:
            note = (
                "Report on-time per the signed-off Route 12 rule; raw fresh telemetry "
                f"is about {lag_minutes:.1f} min behind the two historical traces."
            )
        elif stalled:
            note = (
                f"{latest['ping_id']} is fresh, but the last {STALL_WINDOW_MINUTES} min "
                f"show only {progress_span * 100:.2f}% route progress and speed <=5 km/h."
            )
        elif age_minutes > FRESHNESS_MINUTES and slow_final_pings:
            note = (
                f"Last ping {latest['ping_id']} is {age_minutes:.1f} min old; "
                "the final three pings were all <=5 km/h."
            )
        elif age_minutes > FRESHNESS_MINUTES:
            note = (
                f"Last trusted ping {latest['ping_id']} is {age_minutes:.1f} min old; "
                "no current position is safe to publish."
            )
        elif lag_minutes is not None and lag_minutes >= LATE_THRESHOLD_MINUTES:
            note = (
                f"Fresh ping {latest['ping_id']}; historical equivalent lag "
                f"{lag_minutes:.1f} min."
            )
        else:
            lag_text = "not estimable" if lag_minutes is None else f"{lag_minutes:.1f} min"
            note = (
                f"Fresh ping {latest['ping_id']} at {latest['progress'] * 100:.1f}% "
                f"route progress; historical equivalent lag {lag_text}."
            )

        rows.append(
            {
                "route_id": int(trip["route_id"]),
                "route_name": route["route_name"],
                "trip_id": trip["trip_id"],
                "bookings": booking_count,
                "verdict": verdict,
                "note": note,
                "age_minutes": age_minutes,
                "progress": float(latest["progress"]),
                "lag_minutes": lag_minutes,
                "ping_id": latest["ping_id"],
                "historical_elapsed_minutes": historical_elapsed,
            }
        )

    results = pd.DataFrame(rows).sort_values("route_id")
    raw_pings = inputs.pings
    quality = {
        "pings": len(raw_pings),
        "invalid_recorded_at": int(raw_pings["recorded_at"].isna().sum()),
        "clock_skew_over_120s": int(
            (raw_pings["clock_skew_seconds"] > MAX_CLOCK_SKEW_SECONDS).sum()
        ),
        "invalid_speed": int(
            (~raw_pings["speed_kmph"].between(0, MAX_SPEED_KMPH)).sum()
        ),
        "duplicate_ping_ids": int(
            raw_pings.loc[
                raw_pings["ping_id"].duplicated(keep=False), "ping_id"
            ].nunique()
        ),
        "duplicate_ping_rows": int(
            raw_pings["ping_id"].duplicated(keep=False).sum()
        ),
    }
    return results, quality


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a conservative route lateness read from the Cityflo CSV extract."
    )
    parser.add_argument(
        "--as-of",
        default=DEFAULT_AS_OF,
        help="Timezone-aware timestamp; defaults to the assignment's 07:45 IST.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).parent / "data",
    )
    args = parser.parse_args()

    results, quality = analyze(args.data_dir, args.as_of)
    print(f"As of {args.as_of}")
    display = results[["route_id", "route_name", "verdict", "note"]].copy()
    display["route_name"] = display["route_name"].str.replace("→", " to ", regex=False)
    print(display.to_string(index=False))
    print("\nData quality checks")
    for key, value in quality.items():
        print(f"- {key}: {value}")
    print("\nExcluded operator-7 trips")
    inputs = load_inputs(args.data_dir)
    projected = match_and_project(inputs)
    excluded = [
        trip_id
        for trip_id in inputs.trips.loc[
            inputs.trips["service_date"]
            == pd.Timestamp(args.as_of).tz_convert("Asia/Kolkata").strftime("%Y-%m-%d"),
            "trip_id",
        ]
        if _trip_operator_ids(projected, trip_id) & EXCLUDED_OPERATORS
    ]
    print(", ".join(excluded) if excluded else "none")


if __name__ == "__main__":
    main()
