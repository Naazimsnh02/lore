FROM nginx:alpine
COPY modern-light.html /usr/share/nginx/html/index.html
COPY app-icon.png /usr/share/nginx/html/app-icon.png
COPY hero-bg.png /usr/share/nginx/html/hero-bg.png
# Cloud Run requires port 8080
RUN sed -i 's/listen\s*80;/listen 8080;/g' /etc/nginx/conf.d/default.conf
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
