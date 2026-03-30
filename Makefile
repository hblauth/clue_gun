PY_SRC=apps services shared tests
GO_SRC=services/times_scraper

.PHONY: lint format lint-go format-go build-go migrate load-puzzles extract-words wordfreq

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
	python db/migrate.py

load-puzzles:
	python db/load_puzzles.py

extract-words:
	python services/clue_indexer/extract_words.py

wordfreq:
	python services/clue_indexer/enrich_wordfreq.py