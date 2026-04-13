# Crossword Blog

A monorepo for building a crossword blog with automated content generation and social media posting.

## Architecture

This project consists of multiple services:
- **Times Scraper**: Golang service to scrape crossword data
- **Image Processor**: Python ML service for OCR and image analysis
- **Clue Indexer**: NLP service for word frequency and embeddings
- **Social Bot**: Content generation and social media automation
- **API**: FastAPI service for data access
- **Frontend**: Web interface for the blog

## Development

### Prerequisites
- Python 3.11+
- Go 1.21+
- Docker & Docker Compose
- pre-commit (for Git hooks)

### Quick Start
```bash
# Set up development environment
docker-compose up -d

# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks
pre-commit install
```

## Project Structure

```
crossword_blog/
├── apps/                    # Application services
├── services/               # Core business logic services
├── infra/                  # Infrastructure as code
├── pipelines/              # Data pipeline orchestration
├── db/                     # Database models and schemas
├── shared/                 # Shared utilities and clients
└── tests/                  # Test suites
```

## TODO

### Times Scraper
- [x] Parse crossword HTML into Go structs (clues + answers)
- [x] Save parsed crosswords as local JSON files (1,400+ puzzles scraped)
- [x] Write unit tests for HTML parser

### PostgreSQL Integration
- [ ] Create `crosswords_raw` table (DDL)
- [ ] Build reusable PostgreSQL client (`shared/clients/postgres.py`)
- [ ] Load local JSON files into PostgreSQL
- [ ] Verify inserted data matches schema

### Clue Indexer
- [x] Extract unique words from clues and answers (MVP)
- [ ] Count word frequencies
- [ ] Store word stats in `dictionary_stats` table
- [ ] Write tests for frequency counter
- [ ] % of Collins words used in crosswords
- [ ] Classify words by function (noun, verb, indicator, etc.)
- [ ] Add phrases (multi-word answers and clue fragments)

### Image Processor
- [x] Load crossword photo with OpenCV (HEIC + JPG)
- [x] Normalise clue-list orientation (90° rotation detection)
- [x] Split normalised region into ACROSS / DOWN columns
- [x] Detect ACROSS / DOWN column headings via OCR
- [x] Build clue-number map from puzzle JSON sequence (OCR + row-projection fallback)
- [x] Scan num-zone for star-shaped contours (radial-peaks classifier)
- [x] `validate.py` harness against 83-image ground-truth dataset (F1 = 0.126)
- [x] 52 unit tests across pipeline helpers
- [ ] **Phase 2** — per-clue classification: for each clue in the map, crop the annotation zone and classify as star / no-star (cuts FP rate from digit-pair false positives)
- [ ] **Phase 3** — fix DOWN column `_find_text_start_x` returning 0 for JPG images (scans crossword grid instead of clue text)
- [ ] Integrate star detections with PostgreSQL
- [ ] Handle edge cases: multi-page clue lists, very dark photos, no annotation

### Social Bot
- [x] Generate image cards (clue + metadata rendered to PNG)
- [x] Post to Instagram via Playwright browser automation
- [x] Queue posts via Redis scheduler
- [ ] Post to TikTok
- [ ] Post to X (Twitter)
- [ ] Log post metadata to PostgreSQL

### Frontend
- [ ] Scaffold frontend app (Next.js or SvelteKit)
- [ ] Display clue + media fetched from API
- [ ] Deploy to Vercel or GCP

### Orchestration (optional)
- [ ] Create Airflow DAG for daily scrape → analyse → post pipeline
- [ ] Schedule social bot to run from DAG

### Backlog
- [ ] Look at old Times for the Times website for more puzzles
- [ ] Scrape the Times Crossword Club website for clues and perhaps answers
- [ ] Move to cloud app
- [ ] Add times snitch lookup
- [ ] Add companion - done / not done / time completed etc. integrate with times crossword club?

## Contributing

1. Follow the task-based development approach
2. Write minimal, focused code
3. Ensure all tests pass before committing
4. Use pre-commit hooks for code quality 