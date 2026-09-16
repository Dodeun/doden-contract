FROM node:22-alpine AS build
WORKDIR /app
COPY . .

FROM nginx:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
