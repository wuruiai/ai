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
# G10.10 M8 安全头片段：与 nginx.conf 一起拷入（gzip/缓存/安全头见 nginx.conf）
# .snippet 后缀：不被 conf.d/*.conf 自动加载，仅由 nginx.conf 显式 include
COPY security-headers.snippet /etc/nginx/conf.d/security-headers.snippet

# G10.10 M8 非 root 运行（纵深防御）：nginx 主进程也以 nginx 用户运行
# → 只能监听非特权端口 8080；pid/缓存/日志目录预先归 nginx 所有
RUN touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid \
    && chown -R nginx:nginx /var/cache/nginx \
    && chown -R nginx:nginx /var/log/nginx \
    && chown -R nginx:nginx /usr/share/nginx/html

USER nginx
EXPOSE 8080
# 绕过默认 docker-entrypoint.sh：其 10-listen-on-ipv6 脚本会改写
# /etc/nginx/conf.d/default.conf（非 root 无写权限会失败）；本镜像配置静态，
# 不用模板/envsubst，直接启动 nginx 即可
ENTRYPOINT ["nginx", "-g", "daemon off;"]
