# Next.js (standalone) image.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 AUTH_SECRET=build-time AUTH_DEV_LOGIN=true
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
# V-1: 서버사이드 OG 카드의 한글 폰트 (standalone 트레이싱이 런타임 readFile을 못 따라감)
COPY --from=build /app/assets ./assets
EXPOSE 3000
CMD ["node", "server.js"]
