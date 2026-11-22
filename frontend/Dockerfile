FROM node:20-alpine

WORKDIR /app

# Install deps first (cached layer)
COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
