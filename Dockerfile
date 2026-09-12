FROM alpine
LABEL \
	maintainer="Davide Alberani <da@mimante.net>"

EXPOSE 3210

RUN \
	apk add --no-cache \
		git \
		py3-apscheduler \
		py3-lxml \
		py3-requests \
		py3-sqlalchemy \
		py3-tornado

VOLUME /diffido/conf /diffido/storage

COPY diffido.py /diffido/
COPY dist /diffido/dist/
COPY ssl /diffido/ssl/

WORKDIR /diffido/

ENTRYPOINT ["./diffido.py"]
