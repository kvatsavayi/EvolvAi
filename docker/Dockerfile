FROM node:20-slim AS ui-builder
WORKDIR /ui
COPY apps/ui/package.json apps/ui/vite.config.js apps/ui/index.html ./
COPY apps/ui/src ./src
COPY apps/ui/styles.css ./styles.css
RUN npm install
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV APP_DATA_DIR=/app/data
ENV APP_CONFIG_DIR=/app/config
ENV DATABASE_URL=sqlite:////app/data/db.sqlite
ENV ARTIFACT_DIR=/app/data/artifacts
ENV WORKSPACE_DIR=/app/data/workspaces
COPY . .
COPY --from=ui-builder /ui/dist /app/apps/ui/dist
RUN pip install --no-cache-dir ".[dev]"
RUN mkdir -p /app/data /app/config /app/config/personas && cp -r /app/personas/. /app/config/personas/
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
