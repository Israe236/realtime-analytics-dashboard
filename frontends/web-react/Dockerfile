# syntax=docker/dockerfile:1
# Build context: frontends/ (the app imports ../shared).

FROM node:24-alpine AS build
WORKDIR /app/web-react
COPY web-react/package.json web-react/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY shared /app/shared
COPY web-react ./
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web-react/dist /usr/share/nginx/html
EXPOSE 80
