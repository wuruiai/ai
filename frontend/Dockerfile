# 水利 RAG + Agent 前端镜像（多阶段：node 构建 → nginx 伺服 + 反代）
FROM node:20-alpine AS build
WORKDIR /app
# 必须先复制 .npmrc：否则 npm ci 在容器内走官方源，国内网络下会卡死/超时
COPY .npmrc ./
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
