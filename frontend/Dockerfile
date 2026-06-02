FROM node:24-alpine

WORKDIR /workspace

COPY turkuaz-ui/package.json ./turkuaz-ui/package.json
COPY turkuaz-ui/src ./turkuaz-ui/src
COPY PaymentGateway/frontend/.npmrc ./PaymentGateway/frontend/.npmrc
COPY PaymentGateway/frontend/package.json PaymentGateway/frontend/package-lock.json* ./PaymentGateway/frontend/

WORKDIR /workspace/PaymentGateway/frontend
RUN npm install

COPY PaymentGateway/frontend ./

EXPOSE 7502

CMD ["npm", "run", "dev"]
