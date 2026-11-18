# Cityflo Data Scientist take-home

## 07:45 IST verdicts, 17 June 2026

| Route | Verdict | Note |
|---|---|---|
| 9 - Thane to Powai | HOLD | Fresh ping P-0003264 places the bus at 98.8% of the route; estimated historical-equivalent lag is -0.7 min. |
| 11 - Borivali to BKC | CALL_DRIVER | Fresh ping P-0003518; across the last 20 minutes, progress is only 0.07% of the route and reported speed never exceeds 2.7 km/h. |
| 12 - Mulund to Andheri E | HOLD | The signed-off reporting rule says lateness is 0. Fresh telemetry is about 9.2 min behind the two historical traces; this is a policy override, not evidence of on-time running. |
| 14 - Kandivali to Lower Parel | NO_VERDICT | The latest trustworthy ping, P-0003828, was received at 06:56:57, 48.0 min before the cut-off. The later batch is not available as of 07:45. |
| 17 - Vashi to Worli | CALL_DRIVER | The last ping, P-0004087, is 17.3 min old; the final three pings reported 3.8, 3.9, and 5.0 km/h. Verify the vehicle before sending a rider message. |
| 21 - Ghatkopar to BKC | HOLD | Fresh ping P-0004162 places the in-scope trip at 62.8% of the route; historical-equivalent lag is -0.5 min. The operator-7 trip is excluded. |

No route gets a numeric `PUSH_LATE` message. The reliable evidence supports a driver check for Routes 11 and 17, a hold for Routes 9 and 21, a mandated on-time report for Route 12, and no publishable number for Route 14.

## Ranked worry order

1. **Route 11** - Call the driver first. TRIP_014 has 8 bookings and has been scheduled for 59 minutes, but 60 recent pings keep it within roughly 15 m of the origin with speeds no higher than 2.7 km/h. A rider push cannot resolve a bus that may not have departed.
2. **Route 17** - Call the driver next. TRIP_017 has 16 bookings. Its signal stopped for 17.3 minutes after three slow pings. The last known position is not a current position, so do not tell riders a number.
3. **Route 12** - The official output must remain on-time, but put the conflict in front of the ops lead: TRIP_015 has 9 bookings and is about 9.2 minutes behind the two historical progress traces. The commitment rule suppresses this operational signal.
4. **Route 14** - Escalate the feed/clock issue. TRIP_016 has 12 bookings and no trustworthy update for 48 minutes. The exact 90-minute clock offset on the following batch makes its live state unknowable from this extract.

## Definition and method

The lateness measure is an arrival-time-equivalent progress lag, not a rider-specific ETA: project each ping onto the ordered stop polyline, express the along-route position as a fraction, and compare the current trip's elapsed time since scheduled departure with the elapsed time at which each prior same-route trip reached that fraction. The median of the two prior trips is the reference. A positive value means the current trip took longer to reach the observed position than the historical trips did.

Use the server `received_at` clock to decide whether a ping was available by 07:45. Use `recorded_at` as event time only when the clocks differ by at most 120 seconds. Discard invalid event times, impossible speeds (below 0 or above 120 km/h), and points more than 500 m from the route polyline. The 500 m guard is deliberately much looser than the observed normal cross-track errors (under 18 m) and only removes grossly misplaced points.

Only a reading no more than 5 minutes old can support a numeric rider message. A `PUSH_LATE` requires at least 5 whole minutes of historical-equivalent lag and fresh, trusted evidence. A fresh trip that remains within 1% progress for 20 minutes with speeds no higher than 5 km/h gets `CALL_DRIVER`, not a guessed rider ETA. If the feed is stale and the last three pings are all at or below 5 km/h, call the driver to verify; otherwise return `NO_VERDICT`.

These are triage rules for this extract, not a production ETA model. Two prior days do not establish a stable distribution, stops are a polyline rather than a road map, and distance progress alone does not model stop dwell or future congestion.

## Consequential calls

### Route 11 - CALL_DRIVER

- **Rule:** A fresh 20-minute window with at least three pings, no more than 1% route progress, and no reported speed above 5 km/h is a driver-check signal. Do not send a numeric late estimate until the driver or a reliable current feed explains the stop.
- **Case it breaks on:** TRIP_014 / P-0003518 shows 0.07% progress over the window. GPS could repeatedly snap to the origin or the vehicle could be intentionally staging; the polyline is not map-matched.
- **Cost and who eats it:** A false call costs the depot goodwill. If the bus is actually stopped, 8 booked riders bear the delay; a false push would also spend rider trust on a number we cannot defend.
- **What would flip it:** A fresh sequence showing sustained movement away from the origin, or a driver confirmation that the vehicle is deliberately waiting for a later dispatch.

### Route 17 - CALL_DRIVER

- **Rule:** When the feed is more than 5 minutes stale and its last three trusted pings are all at or below 5 km/h, call to verify but do not publish a late-minute figure.
- **Case it breaks on:** TRIP_017 / P-0004085 through P-0004087 fall to 3.8, 3.9, and 5.0 km/h at about 70.6% route progress, then there are no received pings for 17.3 minutes. A legitimate traffic queue followed by a radio/network dropout would trigger the same rule.
- **Cost and who eats it:** A needless call costs the depot goodwill. Not checking risks leaving 16 booked riders without help if the bus has stopped.
- **What would flip it:** A fresh ping sequence or direct driver confirmation. Until then, a rider-facing number is unsafe.

### Route 14 - NO_VERDICT

- **Rule:** Do not use any event whose server receipt time is after the 07:45 decision cut-off. If the newest trusted ping is over 5 minutes old and does not show a slow tail, publish no lateness number.
- **Case it breaks on:** P-0003828 is the last trusted receipt at 06:56:57 and shows 22.3% progress. P-0003829 through P-0003973 have a consistent 5,400-second recorded/received offset and are received after the cut-off, even though their device times run through 07:44. The offset could be delayed delivery or a bad server clock; either interpretation makes availability uncertain.
- **Cost and who eats it:** Withholding a real delay can leave 12 booked riders waiting without warning. Using future-received rows would create a false sense of live knowledge and potentially a confident wrong message.
- **What would flip it:** Confirmation from the ingest owner that the receipt clock was 90 minutes fast and those rows were already available by 07:45, or a fresh independently timed ping.

### Route 12 - HOLD by reporting rule

- **Rule:** Report lateness as 0 for Route 12, as directed by the signed-off contractual reconciliation rule. Keep the independent movement estimate visible in this decision record; do not silently relabel the operational evidence as on-time.
- **Case it breaks on:** TRIP_015 / P-0003624 is fresh at 07:44:53 with 49.7% route progress. At that position, the two historical trips took 30.7 and 30.9 minutes from scheduled departure; the current trip has elapsed 40 minutes, or about 9.2 minutes more.
- **Cost and who eats it:** The reported hold protects the contractual figure, but can mislead ops and the 9 booked riders if the raw delay is real. A false override costs trust; a suppressed delay costs waiting time.
- **What would flip it:** An authorized change to the reporting rule, or a fresh downstream observation that reconciles the current progress with the route's actual service state.

### Routes 9 and 21 - HOLD

- **Rule:** Hold when the latest trusted ping is fresh and historical-equivalent lag is below 5 minutes. Route 9's latest reading is 98.8% complete and about 0.7 minutes ahead; Route 21's in-scope trip is about 0.5 minutes ahead.
- **Case it breaks on:** Route 9 / P-0003264 is near the end of the route, but a position on the polyline does not prove that the bus has cleared the terminal or that riders have alighted. Route 21 / P-0004162 can also be early on distance while delayed at a later stop.
- **Cost and who eats it:** A false hold strands riders if downstream dwell or congestion appears after the ping. A needless push burns rider trust and the depot must explain a message sent to trips that are currently progressing normally.
- **What would flip it:** A fresh ping showing at least 5 minutes of historical-equivalent lag, or stop-level arrival evidence that contradicts the along-route estimate.

## Reconciliation and data-quality decisions

- **Operator 7:** Exclude operator-7 vehicles and their trips from the lateness output. This removes TRIP_019 on Route 21 and its 6 bookings; Route 21's output is based on TRIP_018 only. This can hide a real service issue, but it is required to match the stated onboarding SOP.
- **Invalid device time:** P-0001797 has `recorded_at=06:60:23` on 16 June. Its receipt time is valid, but the device time cannot be ordered reliably; exclude it from historical progress. A valid normalized device timestamp would change that choice.
- **Impossible GPS/speed:** P-0003148 through P-0003150 report speeds above 6,500 km/h and are tens of kilometres from Route 9. P-0003995 reports -3.8 km/h. Exclude these rows rather than letting them move route progress.
- **Repeated identifier:** P-0002821 appears on two different vehicle/date records. Do not deduplicate globally on `ping_id`; retain records by trip, vehicle, and time. Dropping the Wednesday row would not change Route 9's newest ping but could corrupt other joins.
- **Worry-order limit:** The order prioritizes a confirmed possible stop, then a recent slow-and-silent vehicle, then policy-hidden lag, then a long feed/clock blind spot. A confirmed Route 14 clock misconfiguration or a fresh Route 17 ping could change the order immediately.

## Reproducibility

From this directory, install `requirements.txt`, then run:

```powershell
python analyze.py
python -m unittest -v
```

The default cut-off is the assignment's 07:45 IST. Use `--as-of` for another timezone-aware timestamp.
