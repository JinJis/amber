# Next.js (standalone) image.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# NEXT_PUBLIC_* is inlined at `npm run build` — it must arrive as a BUILD ARG, not runtime env.
# Without this ARG a production build silently ships a keyless Toss billing panel.
ARG NEXT_PUBLIC_TOSS_CLIENT_KEY
ENV NEXT_PUBLIC_TOSS_CLIENT_KEY=$NEXT_PUBLIC_TOSS_CLIENT_KEY
ENV NEXT_TELEMETRY_DISABLED=1 AUTH_SECRET=build-time AUTH_DEV_LOGIN=true
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
# Amber 브랜드 정적 자산 (favicon·아이콘·manifest·og.png) — standalone은 public/을 자동 포함 안 함
COPY --from=build /app/public ./public
# V-1: 서버사이드 OG 카드의 한글 폰트 (standalone 트레이싱이 런타임 readFile을 못 따라감)
COPY --from=build /app/assets ./assets
EXPOSE 3000
CMD ["node", "server.js"]
