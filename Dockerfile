FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 mailmcp
USER 10001
ENTRYPOINT ["python", "-m", "easy_email_mcp.server"]
