# Development

This repository uses a `src` layout for the Python package. The application package is located at `src/lyra`.

## Local Setup

1. Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

3. Configure environment variables:

```bash
cp .env.example .env
# Edit .env
```

## Running Tests

```bash
pytest
```

## Package Entry Points

- Run the service:
  ```bash
  python -m lyra
  ```
- Run interactive mode:
  ```bash
  python -m lyra --interactive
  ```

## Code Structure

- `src/lyra`: Core package
- `src/lyra/api`: External API clients
- `src/lyra/database`: SQLite persistence and scanning
- `src/lyra/downloader`: yt-dlp download and metadata tagging
- `src/lyra/orchestrator`: High-level workflow orchestration
- `src/lyra/cli`: Interactive command-line tools

## Formatting and Static Checks

Use `python -m pytest` for tests and use your preferred formatter or linter against `src/lyra`.
