FROM node:22.16.0-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY server ./server
COPY scenes.json ./
COPY tests ./tests
USER node
EXPOSE 3100
CMD ["node", "server/index.mjs"]
