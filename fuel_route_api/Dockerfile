FROM python:latest

WORKDIR /usr/src/app
RUN apt-get update && apt-get install nginx vim -y --no-install-recommends
COPY nginx.default /etc/nginx/sites-available/default
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log
COPY ./requirements.txt /usr/src/app
RUN pip install -r requirements.txt
RUN pip install uwsgi
RUN pip install gunicorn
RUN chown -R www-data:www-data /usr/src/app

COPY . /usr/src/app

EXPOSE 80
STOPSIGNAL SIGTERM

ENTRYPOINT ["sh", "/usr/src/app/start-server.sh"]
