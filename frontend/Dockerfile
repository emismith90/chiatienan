FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# 512MB droplet: let V8 grow its heap into the 2GB swapfile so `next build`
# doesn't hit the auto-sized heap ceiling and OOM (SIGABRT).
ENV NODE_OPTIONS=--max-old-space-size=2048
RUN npm run build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["node", "server.js"]
