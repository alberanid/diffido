#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from tornado.ioloop import IOLoop
from lxml.html.diff import htmldiff
from apscheduler.schedulers.tornado import TornadoScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

CONF_DIR = ''
JOBS_STORE = 'sqlite:///jobs.db'


def serve():
    jobstores = {'default': SQLAlchemyJobStore(url=JOBS_STORE)}
    scheduler = TornadoScheduler(jobstores=jobstores)
    scheduler.start()
    try:
        IOLoop.instance().start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == '__main__':
    serve()
