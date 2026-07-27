FROM node:18-alpine

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy application
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host"]
