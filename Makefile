PY_SRC=apps services shared tests
GO_SRC=services/times_scraper

.PHONY: lint format lint-go format-go build-go migrate load-puzzles load-wordfreq extract-words wordfreq social-scheduler social-worker bot api

lint:
	ruff $(PY_SRC)

format:
	black $(PY_SRC)

lint-go:
	cd $(GO_SRC) && golangci-lint run

format-go:
	cd $(GO_SRC) && gofmt -w .

build-go:
	cd $(GO_SRC) && go build -o times_scraper ./...

migrate:
	@for f in db/migrations/*.sql; do \
		echo "==> $$f"; \
		docker exec -i clue_gun-postgres-1 psql -U crossword -d crossword < "$$f"; \
	done

load-puzzles:
	python3 services/clue_indexer/load_puzzles.py

load-wordfreq:
	python3 services/clue_indexer/load_wordfreq.py

extract-words:
	python3 services/clue_indexer/extract_words.py

wordfreq:
	python3 services/clue_indexer/enrich_wordfreq.py

api:
	python3 -m uvicorn apps.api.main:app --reload --port 8000

social-scheduler:
	python3 services/social_bot/scheduler.py

social-worker:
	python3 services/social_bot/worker.py

# Run scheduler and worker together in the foreground (Ctrl-C stops both)
bot:
	python3 services/social_bot/scheduler.py & python3 services/social_bot/worker.py; kill %1 2>/dev/null
