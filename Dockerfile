FROM alpine
LABEL \
	maintainer="Davide Alberani <da@erlug.linux.it>"

EXPOSE 3210

RUN \
	apk add --update \
		git \
		py3-lxml \
		py3-pip \
		py3-requests \
		py3-sqlalchemy \
		py3-tornado \
	&& pip3 install apscheduler \
	&& rm -rf /var/cache/apk/*

VOLUME /diffido/conf /diffido/storage

COPY diffido.py /diffido/
COPY dist /diffido/dist/
COPY ssl /diffido/ssl/

WORKDIR /diffido/

ENTRYPOINT ["./diffido.py"]

