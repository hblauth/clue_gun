# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`clue_gun` is a crossword blog monorepo with automated content generation and social media posting. The system scrapes crossword data, processes images (OCR/grid detection), indexes clues, and publishes content to social platforms.

## Commands

### Python
```bash
make lint        # Ruff linting on Python code
make format      # Black formatting

pip install -r requirements.txt  # Install dependencies
pytest           # Run tests (tests/ directory)
```

### Go
```bash
make lint-go     # golangci-lint
make format-go   # gofmt

cd services/times_scraper && go build ./...
cd services/times_scraper && go test ./...
```

### Docker (local dev)
```bash
docker-compose up -d   # Start Redis + dev containers
```

### Pre-commit
```bash
pre-commit install   # Install hooks (Black, Ruff, golangci-lint, gofmt)
```

## Architecture

**Data flow:**
1. **Times Scraper** (Go) → scrapes Times crossword site → PostgreSQL
2. **Image Processor** (Python) → OCR/grid detection on photos → PostgreSQL + Cloud Storage
3. **Clue Indexer** (Python) → NLP/embeddings on historical clues → PostgreSQL
4. **Social Bot** (Python) → reads from PostgreSQL/Redis → posts to TikTok, Instagram, X, YouTube
5. **API Service** (Python/FastAPI) → serves **Frontend** (planned, likely Next.js)

**State storage:**
- **PostgreSQL**: crosswords, clues, word dictionary
- **Redis**: social media posting queue, word frequency cache
- **GCS/S3**: ML outputs (JSON/Parquet), media assets

## Project Status

Most services are scaffolded but unimplemented. The `services/times_scraper` Go CLI has basic HTML fetching via colly/cobra. `apps/api` is a Python/FastAPI stub. See `tasks.yml` for the full staged roadmap and `architecture.md` for deeper design documentation.

## Languages & Tools

- **Python 3.11+**: FastAPI, Pydantic, SQLAlchemy, OpenCV, Pytesseract, Pillow, Redis client, PostgreSQL connector
- **Go 1.21+**: gocolly/v2, spf13/cobra
- **Linting**: Ruff (rules E, F, B), Black (line length 88), golangci-lint (govet, errcheck, staticcheck, unused, ineffassign, goimports)
