# syntax=docker/dockerfile:1
# Build context: frontends/ (the app imports ../shared).
# Builds the web version of the Expo app; the native app runs on a phone via Expo Go.

FROM node:24-alpine AS build
WORKDIR /app/mobile-react-native
COPY mobile-react-native/package.json mobile-react-native/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY shared /app/shared
COPY mobile-react-native ./
ENV EXPO_NO_TELEMETRY=1 CI=1
RUN npx expo export --platform web --output-dir dist

FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/mobile-react-native/dist /usr/share/nginx/html
EXPOSE 80
