FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM nginx:1.27-alpine AS runner
RUN printf 'server {\n  listen 3000;\n  server_name _;\n  root /usr/share/nginx/html;\n  index index.html;\n  location / {\n    try_files $uri $uri.html $uri/ /index.html;\n  }\n}\n' > /etc/nginx/conf.d/default.conf
COPY --from=builder /app/out /usr/share/nginx/html
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
