FROM node:24-alpine AS build

WORKDIR /workspace

COPY turkuaz-ui/package.json ./turkuaz-ui/package.json
COPY turkuaz-ui/src ./turkuaz-ui/src
COPY PaymentGateway/frontend/.npmrc ./PaymentGateway/frontend/.npmrc
COPY PaymentGateway/frontend/package.json PaymentGateway/frontend/pnpm-lock.yaml PaymentGateway/frontend/pnpm-workspace.yaml ./PaymentGateway/frontend/

WORKDIR /workspace/PaymentGateway/frontend
RUN corepack enable \
    && corepack prepare pnpm@11.9.0 --activate \
    && pnpm install --frozen-lockfile

COPY PaymentGateway/frontend ./
RUN pnpm build

FROM nginx:1.27-alpine

COPY PaymentGateway/frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /workspace/PaymentGateway/frontend/dist /usr/share/nginx/html

EXPOSE 7502
