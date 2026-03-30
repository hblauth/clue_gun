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
- [ ] Parse crossword HTML into Go structs (clues + answers) ← in progress
- [ ] Save parsed crosswords as local JSON files
- [ ] Write unit tests for HTML parser

### PostgreSQL Integration
- [ ] Create `crosswords_raw` table (DDL)
- [ ] Build reusable PostgreSQL client (`shared/clients/postgres.py`)
- [ ] Load local JSON files into PostgreSQL
- [ ] Verify inserted data matches schema

### Clue Indexer
- [ ] Extract unique words from clues and answers (MVP)
- [ ] Count word frequencies
- [ ] Store word stats in `dictionary_stats` table
- [ ] Write tests for frequency counter
- [ ] % of Collins words used in crosswords
- [ ] Classify words by function (noun, verb, indicator, etc.)
- [ ] Add phrases (multi-word answers and clue fragments)

### Image Processor
- [ ] Load and display crossword photo with OpenCV
- [ ] Detect highlighted squares
- [ ] Map highlights to clue numbers
- [ ] OCR clue text from image regions
- [ ] Save OCR results as JSON
- [ ] Write tests for OCR output

### Social Bot
- [ ] Generate templated captions from clues
- [ ] Select visual asset per clue
- [ ] Render short video clip (text + asset → MP4)
- [ ] Queue video + metadata for posting
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

## Contributing

1. Follow the task-based development approach
2. Write minimal, focused code
3. Ensure all tests pass before committing
4. Use pre-commit hooks for code quality 