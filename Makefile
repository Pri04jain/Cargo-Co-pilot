.PHONY: up down build seed test eval lint format migrate shell-api shell-db logs

# ── Infrastructure ─────────────────────────────────────────────────────────────
up:
	@echo "→ Starting all services..."
	cp -n .env.example .env 2>/dev/null || true
	docker compose up --build -d
	@echo "→ Waiting for services to be ready..."
	sleep 5
	$(MAKE) migrate
	@echo "✓ Cargo Copilot is running at http://localhost:5173 (UI) and http://localhost:8000 (API)"

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ── Database ───────────────────────────────────────────────────────────────────
migrate:
	@echo "→ Running Alembic migrations..."
	docker compose exec api alembic upgrade head

migrate-create:
	@echo "Usage: make migrate-create MSG='describe your migration'"
	docker compose exec api alembic revision --autogenerate -m "$(MSG)"

# ── Seeding ────────────────────────────────────────────────────────────────────
seed:
	@echo "→ Seeding demo data (tenants, users, POs, sample docs)..."
	docker compose exec api python scripts/demo_data.py

# ── Testing ────────────────────────────────────────────────────────────────────
test:
	@echo "→ Running backend tests..."
	docker compose exec api pytest tests/ -v --tb=short

test-unit:
	docker compose exec api pytest tests/unit/ -v --tb=short

test-integration:
	docker compose exec api pytest tests/integration/ -v --tb=short

# ── Evaluation ────────────────────────────────────────────────────────────────
eval-check: ## Validate eval setup before running
	@echo "→ Validating evaluation setup..."
	docker compose exec api python eval/validate_setup.py

eval:
	@echo "→ Running evaluation harness..."
	docker compose exec api python eval/run_eval.py --split test --model gpt-4o-mini
	@echo "✓ Report saved to eval/reports/"

eval-full: ## Run comprehensive evaluation with multiple models
	@echo "→ Running full model comparison evaluation..."
	docker compose exec api mkdir -p eval/reports
	docker compose exec api python eval/run_eval.py --split test --model gpt-4o-mini --max-docs 50
	docker compose exec api python eval/run_eval.py --split test --model gpt-4o --max-docs 20
	@echo "✓ Multi-model evaluation complete!"

eval-dashboard: ## Generate interactive evaluation dashboard
	@echo "→ Generating evaluation dashboard..."
	docker compose exec api python eval/dashboard.py eval/reports/eval_*.json
	@echo "✓ Dashboard generated! Open eval/reports/dashboard.html in your browser."

eval-cost: ## Run cost analysis report (requires Langfuse)
	@echo "→ Running cost analysis..."
	@if [ -z "$$LANGFUSE_PUBLIC_KEY" ] || [ -z "$$LANGFUSE_SECRET_KEY" ]; then \
		echo "❌ Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set"; \
		echo "Cost tracking requires Langfuse credentials to access real cost data."; \
		exit 1; \
	fi
	@echo "Cost data is tracked during 'make eval' runs."
	@echo "View detailed cost analysis in your Langfuse dashboard:"
	@echo "  https://cloud.langfuse.com"
	@echo ""
	@echo "To analyze costs from recent eval runs:"
	@echo "  1. Run: make eval"
	@echo "  2. Check Langfuse dashboard for cost breakdown by document type"
	@echo "  3. Review eval/reports/*.json for per-document costs"

eval-baseline: ## Run baseline evaluation for performance benchmarking
	@echo "→ Running baseline evaluation..."
	docker compose exec api mkdir -p eval/reports/baselines
	docker compose exec api python eval/run_eval.py --split test --model gpt-4o-mini --max-docs 10 > eval/reports/baselines/baseline_$(shell date +%Y%m%d).log
	@echo "✓ Baseline evaluation saved."

# ── Code quality ──────────────────────────────────────────────────────────────
lint:
	docker compose exec api ruff check app/ tests/

format:
	docker compose exec api ruff format app/ tests/

# ── Dev shells ────────────────────────────────────────────────────────────────
shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec db psql -U cargo cargo_copilot

# ── Synthetic data generation ─────────────────────────────────────────────────
generate-data:
	@echo "→ Generating synthetic documents..."
	docker compose exec api python /app/../data/synthetic/generate.py
