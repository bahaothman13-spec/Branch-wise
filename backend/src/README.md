# BranchWise Python source

Application components live in the `branchwise` package, grouped by domain:

- `suppliers`: supplier validation and provisional routes.
- `purchasing`: orders, receiving and purchase history.
- `transfers`: request, decision, receiving and cancellation validation.
- `ai`: configuration, provider/client integration, questions and decision inputs.
- `forecasting`: sales history, forecasts and reliability checks.
- `replenishment`: recommendations and alternative comparisons.
- `reports`: sales, inventory, branch comparisons and CSV export.
- `dashboard`: stock alert summaries.

Use explicit imports, for example:

```python
from branchwise.purchasing.purchase_order_routes import purchase_order_bp
```

`backend/pytest.ini` adds `backend/src` to the test import path. Standalone
examples add it explicitly. Other scripts need `backend/src` on `PYTHONPATH`.
Tests, examples and Postman assets remain outside the source package.
`backend/app.py` and `backend/db.py` remain in their original locations;
provisional Blueprints remain unregistered.

Run tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -c backend\pytest.ini backend -q
```
