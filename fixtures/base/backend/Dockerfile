FROM node:22-alpine AS build
WORKDIR /app
COPY . .

FROM node:22-alpine
WORKDIR /app
COPY --from=build /app /app
CMD ["node", "index.js"]
