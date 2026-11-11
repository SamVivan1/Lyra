# Deployment

Lyra is designed to run reliably in a containerized homelab environment.

## Docker

Build and run the container:

```bash
docker-compose up -d --build
```

The service uses the `Dockerfile` in the repository and loads configuration from `.env`.

## Runtime Environment

The Docker image installs the package with `pip install .`, so it can be executed as:

```bash
python -m lyra
```

## Volumes

Mount:

- Your music library path into the container if using downloads that place files directly into the library.
- A local `./data` directory to persist `music_automation.db`.

Example `docker-compose.yml` volume mapping for multiple library roots:

```yaml
volumes:
  - /mnt/Storage2/data/media/Music:/mnt/Storage2/data/media/Music
  - /mnt/Storage1/data/media/Music:/mnt/Storage1/data/media/Music
  - /DATA/Downloads:/DATA/Downloads
  - ./data:/app/data
```

Set `LIBRARY_PATH` in `.env` to include both mounted paths, for example:

```env
LIBRARY_PATH=/mnt/Storage2/data/media/Music:/mnt/Storage1/data/media/Music
```

## Logging

Logs are written to the container stdout and to `music_automation.log` inside the working directory.

## Production Recommendations

- Use `restart: unless-stopped` for service resiliency.
- Configure a host-level backup for the SQLite data directory.
- Use `docker-compose logs -f lyra` for live troubleshooting.
