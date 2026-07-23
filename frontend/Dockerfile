# Frontend image - multi-stage:
#   1) Node builds the Vite SPA (`npm run build`).
#   2) nginx serves the static `dist/` and proxies `/api/*` to the backend service.
#
#   docker build -t ibkr-frontend ./frontend
#   docker compose build frontend
FROM node:22-alpine AS build

WORKDIR /app

# Install deps from the lockfile for reproducibility. `npm ci` requires
# package-lock.json to be present.
COPY package.json package-lock.json ./
RUN npm ci

# Source.
COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts index.html ./
COPY src/ ./src/

# Production build -> /app/dist
RUN npm run build

# ---------- Stage 2: nginx ----------
FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache curl

# Replace the default site config with our SPA + /api proxy.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy the built SPA.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1/ -o /dev/null || exit 1
