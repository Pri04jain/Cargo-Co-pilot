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
eval:
	@echo "→ Running evaluation harness..."
	docker compose exec api python eval/run_eval.py
	@echo "✓ Report saved to eval/reports/"

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
