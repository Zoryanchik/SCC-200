FROM node:18-bullseye
WORKDIR /app

COPY ./transport-frontend/package.json ./transport-frontend/package-lock.json* /app/
COPY ./transport-frontend /app

RUN npm install --legacy-peer-deps

EXPOSE 5173
CMD ["sh", "-c", "npm run dev -- --host 0.0.0.0"]
