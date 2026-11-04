# The workbench. The API address is fixed when the app is built, so it is a build argument:
# docker build -f frontend/Dockerfile --build-arg NEXT_PUBLIC_LANE_API_URL=https://<site> .
FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ARG NEXT_PUBLIC_LANE_API_URL
# Real sign-in (optional): Cognito's managed-login domain and the app client. Empty: demo people.
ARG NEXT_PUBLIC_LANE_COGNITO_DOMAIN=""
ARG NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID=""
RUN test -n "$NEXT_PUBLIC_LANE_API_URL" || (echo "NEXT_PUBLIC_LANE_API_URL is required" && exit 1)
ENV NEXT_PUBLIC_LANE_API_URL=$NEXT_PUBLIC_LANE_API_URL \
    NEXT_PUBLIC_LANE_COGNITO_DOMAIN=$NEXT_PUBLIC_LANE_COGNITO_DOMAIN \
    NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID=$NEXT_PUBLIC_LANE_COGNITO_CLIENT_ID \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node /app ./
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000", "-H", "0.0.0.0"]
