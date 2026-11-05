FROM caddy:2.10.2-alpine
RUN setcap -r /usr/bin/caddy
ENV PORT=3000
COPY deploy/render-redirect.Caddyfile /etc/caddy/Caddyfile
EXPOSE 3000
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
