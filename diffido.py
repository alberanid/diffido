#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging

from tornado.ioloop import IOLoop
# from lxml.html.diff import htmldiff
from apscheduler.schedulers.tornado import TornadoScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import tornado.httpserver
import tornado.ioloop
import tornado.options
from tornado.options import define, options
import tornado.web
from tornado import gen, escape

CONF_DIR = ''
JOBS_STORE = 'sqlite:///jobs.db'
API_VERSION = '1.0'


class DiffidoBaseException(Exception):
    """Base class for diffido custom exceptions.

    :param message: text message
    :type message: str
    :param status: numeric http status code
    :type status: int"""
    def __init__(self, message, status=400):
        super(DiffidoBaseException, self).__init__(message)
        self.message = message
        self.status = status


class BaseHandler(tornado.web.RequestHandler):
    """Base class for request handlers."""
    # Cache currently connected users.
    _users_cache = {}

    # set of documents we're managing (a collection in MongoDB or a table in a SQL database)
    document = None
    collection = None

    # A property to access the first value of each argument.
    arguments = property(lambda self: dict([(k, v[0].decode('utf-8'))
                                            for k, v in self.request.arguments.items()]))

    @property
    def clean_body(self):
        """Return a clean dictionary from a JSON body, suitable for a query on MongoDB.

        :returns: a clean copy of the body arguments
        :rtype: dict"""
        return escape.json_decode(self.request.body or '{}')

    def write_error(self, status_code, **kwargs):
        """Default error handler."""
        if isinstance(kwargs.get('exc_info', (None, None))[1], DiffidoBaseException):
            exc = kwargs['exc_info'][1]
            status_code = exc.status
            message = exc.message
        else:
            message = 'internal error'
        self.build_error(message, status=status_code)

    def is_api(self):
        """Return True if the path is from an API call."""
        return self.request.path.startswith('/v%s' % API_VERSION)

    def initialize(self, **kwargs):
        """Add every passed (key, value) as attributes of the instance."""
        for key, value in kwargs.items():
            setattr(self, key, value)

    def build_error(self, message='', status=400):
        """Build and write an error message.

        :param message: textual message
        :type message: str
        :param status: HTTP status code
        :type status: int
        """
        self.set_status(status)
        self.write({'error': True, 'message': message})


class RootHandler(BaseHandler):
    """Handler for the / path."""
    app_path = os.path.join(os.path.dirname(__file__), "dist")

    @gen.coroutine
    def get(self, *args, **kwargs):
        # serve the ./dist/index.html file
        with open(self.app_path + "/index.html", 'r') as fd:
            self.write(fd.read())


def serve():
    jobstores = {'default': SQLAlchemyJobStore(url=JOBS_STORE)}
    scheduler = TornadoScheduler(jobstores=jobstores)
    scheduler.start()

    define("port", default=3210, help="run on the given port", type=int)
    define("address", default='', help="bind the server at the given address", type=str)
    define("ssl_cert", default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_cert.pem'),
            help="specify the SSL certificate to use for secure connections")
    define("ssl_key", default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_key.pem'),
            help="specify the SSL private key to use for secure connections")
    define("debug", default=False, help="run in debug mode")
    define("config", help="read configuration file",
            callback=lambda path: tornado.options.parse_config_file(path, final=False))
    tornado.options.parse_command_line()

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if options.debug:
        logger.setLevel(logging.DEBUG)

    ssl_options = {}
    if os.path.isfile(options.ssl_key) and os.path.isfile(options.ssl_cert):
        ssl_options = dict(certfile=options.ssl_cert, keyfile=options.ssl_key)

    init_params = dict(listen_port=options.port, logger=logger, ssl_options=ssl_options)

    # _days_path = r"/days/?(?P<day>[\d_-]+)?"
    application = tornado.web.Application([
            # (_days_path, DaysHandler, init_params),
            # (r'/v%s%s' % (API_VERSION, _days_path), DaysHandler, init_params),
            (r"/(?:index.html)?", RootHandler, init_params),
            (r'/?(.*)', tornado.web.StaticFileHandler, {"path": "dist"})
        ],
        static_path=os.path.join(os.path.dirname(__file__), "dist/static"),
        debug=options.debug)
    http_server = tornado.httpserver.HTTPServer(application, ssl_options=ssl_options or None)
    logger.info('Start serving on %s://%s:%d', 'https' if ssl_options else 'http',
                                                 options.address if options.address else '127.0.0.1',
                                                 options.port)
    http_server.listen(options.port, options.address)

    try:
        IOLoop.instance().start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == '__main__':
    serve()
