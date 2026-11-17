# Backend

## Layout

- `src/`: application code and shared Python modules.
- `tests/`: automated tests.
- `examples/`: runnable demonstrations.
- `postman/`: API collection and usage notes.

Run these commands from the repository root:

```powershell
.\.venv\Scripts\python.exe -m flask --app backend.src.app run
.\.venv\Scripts\python.exe -m pytest backend
.\.venv\Scripts\python.exe backend/examples/purchasing_demo.py
.\.venv\Scripts\python.exe backend/examples/forecast_replenishment_demo.py
```

## Task groups (in task order)

| Tasks | Source directory |
| --- | --- |
| BE-28 | `src/branchwise/suppliers/` |
| BE-29?31 | `src/branchwise/purchasing/` |
| BE-34?37 | `src/branchwise/transfers/` |
| BE-38 | `src/branchwise/ai/` |
| BE-39?40 | `src/branchwise/forecasting/` |
| BE-41?42 | `src/branchwise/replenishment/` |
| BE-43?44 | `src/branchwise/ai/` |
| BE-45 | `src/branchwise/forecasting/` |
| BE-46 | `src/branchwise/dashboard/` |
| BE-47?50 | `src/branchwise/reports/` |

`src/app.py` is the Flask entry point; `src/db.py` is the shared database module.
See `DEV2_HANDOFF.md` for each task's individual modules and tests.
