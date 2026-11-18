# Cityflo Data Scientist Take-Home

A reproducible analysis of the synthetic Cityflo morning GPS extract for the 07:45 IST decision on 17 June 2026.

## Run

```powershell
python -m pip install -r requirements.txt
python analyze.py
python -m unittest -v
```

The script defaults to the assignment's 07:45 IST cut-off. To analyze another time:

```powershell
python analyze.py --as-of "2026-06-17T07:45:00+05:30"
```

## Contents

- `analyze.py` — input validation, route projection, historical progress comparison, and conservative verdicts.
- `decision_record.md` — route verdicts, prioritized follow-up, decision rules, edge cases, and costs.
- `decisions.jsonl` — steering ledger for the agent session.
- `../data/` — the supplied synthetic CSV extract.
- `BRIEF.md`, `DATA_GUIDE.md`, `HANDOFF.md` — assignment materials.
- `test_analyze.py` — focused checks for the timestamp, projection, and decision rules.

The resume and raw agent session log are intentionally not included. The resume contains personal information, and the raw log contains an exposed bearer credential.

## Important limits

The progress comparison is a small-sample triage heuristic based on two prior trips per route, not a production ETA. The Route 12 output follows the supplied reporting convention while explicitly noting the conflicting live estimate. See `decision_record.md` for the full reasoning.
