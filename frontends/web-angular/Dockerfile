# syntax=docker/dockerfile:1
# Build context: frontends/ (the app imports ../shared).

FROM node:24-alpine AS build
WORKDIR /app/web-angular
COPY web-angular/package.json web-angular/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY shared /app/shared
COPY web-angular ./
RUN npx ng build --configuration production

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/web-angular/dist/web-angular/browser /usr/share/nginx/html
EXPOSE 80
