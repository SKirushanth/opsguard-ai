# Release checkpoint

Source: main, commit 9ace5e8. Visitor UI and isolated incidents were already live in demo-1127r2. This release adds temporary simulated service IDs for hosted agent tool reads, verifies receipts against those reads, and labels each answer by its source. WSO2 agent source and configuration are unchanged.

The production federated credential now matches GitHub’s ID-bound repository subject. The earlier OIDC failure is resolved; rerunning the failed production job succeeded (run 46006982589). Azure deployment uses the existing CI workflow, main-only production environment, private backend, and existing ACR. See [pipeline.md](pipeline.md) for rollback, record guards and the manual WSO2 checkpoint.

Release run: https://github.com/SKirushanth/opsguard-ai/actions/runs/46008068195 . If interrupted, inspect that run first. If it is still active, wait. If it failed, inspect the failing step and actual ready revisions before retrying. Do not rebuild the agent or rotate any keys. Verify one live visitor investigation and one /api/public-demo/analysis/local response; sessions and incidents used for verification must be disposable.

First activation attempt (46008068195) was rejected by ARM for a read/write API schema mismatch before either serving revision changed. Repair commit 9b3b27b uses the same 2025-01-01 schema end to end and reuses SHA-tagged images when only workflow code changes. Follow https://github.com/SKirushanth/opsguard-ai/actions/runs/46008793262 instead of retrying the old run.
