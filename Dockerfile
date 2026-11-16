FROM python:3.12-alpine

WORKDIR /app

COPY src/ /app/src/
COPY pyproject.toml README.md /app/

RUN pip install --no-cache-dir .

# Note: Wake-on-LAN broadcasts require host networking.
# Run with `docker run --net=host ...`
ENTRYPOINT ["wake-on-prompt"]
CMD ["--config", "/etc/wake-on-prompt/config.json"]
