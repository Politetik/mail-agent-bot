# syntax=docker/dockerfile:1
FROM node:24-alpine

WORKDIR /app

# Dependencies first for layer caching.
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev

COPY src ./src

ENV NODE_ENV=production
ENV DB_PATH=/app/data/mail-agent.db

# The SQLite database lives here; mount a volume to persist it.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["node", "src/index.js"]
