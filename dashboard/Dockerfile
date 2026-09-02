FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM build AS test
CMD ["sh", "-c", "npm run check && npm test -- --run"]

FROM build AS codegen
ENV OPENAPI_SCHEMA=http://server:8000/openapi.json
CMD ["npm", "run", "generate:api"]

FROM mcr.microsoft.com/playwright:v1.62.1-noble AS e2e
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
CMD ["npm", "run", "test:e2e"]

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
COPY --from=build /app/build ./build
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
EXPOSE 3000
CMD ["node", "build"]
