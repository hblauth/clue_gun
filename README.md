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
- Snowflake account

### Quick Start
```bash
# Set up development environment
docker-compose up -d

# Install dependencies
pip install -r requirements.txt
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

## Contributing

1. Follow the task-based development approach
2. Write minimal, focused code
3. Ensure all tests pass before committing
4. Use pre-commit hooks for code quality 