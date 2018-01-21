#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import io
import json
import shutil
import urllib
import logging
import datetime
import requests
import subprocess
import multiprocessing

from tornado.ioloop import IOLoop
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.tornado import TornadoScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import tornado.httpserver
import tornado.ioloop
import tornado.options
from tornado.options import define, options
import tornado.web
from tornado import gen, escape

JOBS_STORE = 'sqlite:///conf/jobs.db'
API_VERSION = '1.0'
SCHEDULES_FILE = 'conf/schedules.json'
GIT_CMD = 'git'

re_insertion = re.compile(r'(\d+) insertion')
re_deletion = re.compile(r'(\d+) deletion')

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def read_schedules():
    if not os.path.isfile(SCHEDULES_FILE):
        return {'schedules': {}}
    try:
        with open(SCHEDULES_FILE, 'r') as fd:
            return json.loads(fd.read())
    except Exception as e:
        logger.error('unable to read %s: %s' % (SCHEDULES_FILE, e))
        return {'schedules': {}}


def write_schedules(schedules):
    with open(SCHEDULES_FILE, 'w') as fd:
        fd.write(json.dumps(schedules, indent=2))


def next_id(schedules):
    ids = schedules.get('schedules', {}).keys()
    if not ids:
        return '1'
    return str(max([int(i) for i in ids]) + 1)


def get_schedule(id_, addID=True):
    schedules = read_schedules()
    data = schedules.get('schedules', {}).get(id_, {})
    if addID:
        data['id'] = str(id_)
    return data


def run_job(id_=None, *args, **kwargs):
    schedule = get_schedule(id_, addID=False)
    url = schedule.get('url')
    if not url:
        return
    logger.debug('Running job id:%s title:%s url: %s' % (id_, schedule.get('title', ''), url))
    req = requests.get(url, allow_redirects=True, timeout=(30.10, 240))
    content = req.text
    req_path = urllib.parse.urlparse(req.url).path
    base_name = os.path.basename(req_path) or 'index'
    def _commit(id_, filename, content):
        os.chdir('storage/%s' % id_)
        with open(filename, 'w') as fd:
            fd.write(content)
        p = subprocess.Popen([GIT_CMD, 'add', filename])
        p.communicate()
        p = subprocess.Popen([GIT_CMD, 'commit', '-m', '%s' % datetime.datetime.utcnow(), '--allow-empty'])
        p.communicate()
    p = multiprocessing.Process(target=_commit, args=(id_, base_name, content))
    p.start()
    p.join()


def get_history(id_):
    def _history(id_, queue):
        os.chdir('storage/%s' % id_)
        p = subprocess.Popen([GIT_CMD, 'log', '--pretty=oneline', '--shortstat'], stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        queue.put(stdout)
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_history, args=(id_, queue))
    p.start()
    res = queue.get().decode('utf-8')
    p.join()
    history = []
    res_io = io.StringIO(res)
    while True:
        commit_line = res_io.readline().strip()
        if not commit_line:
            break
        commit_id, message = commit_line.split(' ', 1)
        if len(commit_id) != 40:
            continue
        changes_line = res_io.readline().strip()
        insert = re_insertion.findall(changes_line)
        if insert:
            insert = int(insert[0])
        else:
            insert = 0
        delete = re_deletion.findall(changes_line)
        if delete:
            delete = int(delete[0])
        else:
            delete = 0
        history.append({'id': commit_id, 'message': message, 'insertions': insert, 'deletions': delete,
                        'changes': max(insert, delete)})
    return history

def scheduler_update(scheduler, id_):
    schedule = get_schedule(id_, addID=False)
    if not schedule:
        return
    trigger = schedule.get('trigger')
    if trigger not in ('interval', 'cron'):
        return
    args = {}
    if trigger == 'interval':
        args['trigger'] = 'interval'
        for unit in 'weeks', 'days', 'hours', 'minutes', 'seconds':
            if 'interval_%s' % unit not in schedule:
                continue
            args[unit] = int(schedule['interval_%s' % unit])
    elif trigger == 'cron':
        cron_trigger = CronTrigger.from_crontab(schedule.get('cron_crontab'))
        args['trigger'] = cron_trigger
    git_create_repo(id_)
    scheduler.add_job(run_job, id=id_, replace_existing=True, kwargs={'id_': id_}, **args)


def scheduler_delete(scheduler, id_):
    scheduler.remove_job(job_id=id_)
    git_delete_repo(id_)


def reset_from_schedules(scheduler):
    scheduler.remove_all_jobs()
    for key in read_schedules().get('schedules', {}).keys():
        scheduler_update(scheduler, id_=key)


def git_create_repo(id_):
    repo_dir = 'storage/%s' % id_
    if os.path.isdir(repo_dir):
        return
    p = subprocess.Popen([GIT_CMD, 'init', repo_dir])
    p.communicate()


def git_delete_repo(id_):
    repo_dir = 'storage/%s' % id_
    if not os.path.isdir(repo_dir):
        return
    shutil.rmtree(repo_dir)


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

    def build_success(self, message='', status=200):
        """Build and write a success message.

        :param message: textual message
        :type message: str
        :param status: HTTP status code
        :type status: int
        """
        self.set_status(status)
        self.write({'error': False, 'message': message})


class SchedulesHandler(BaseHandler):
    @gen.coroutine
    def get(self, id_=None, *args, **kwargs):
        if id_ is not None:
            self.write({'schedule': get_schedule(id_)})
            return
        schedules = read_schedules()
        self.write(schedules)

    @gen.coroutine
    def put(self, id_=None, *args, **kwargs):
        if id_ is None:
            return self.build_error(message='update action requires an ID')
        data = self.clean_body
        schedules = read_schedules()
        if id_ not in schedules.get('schedules', {}):
            return self.build_error(message='schedule %s not found' % id_)
        schedules['schedules'][id_] = data
        write_schedules(schedules)
        scheduler_update(scheduler=self.scheduler, id_=id_)
        self.write(get_schedule(id_=id_))

    @gen.coroutine
    def post(self, *args, **kwargs):
        data = self.clean_body
        schedules = read_schedules()
        id_ = next_id(schedules)
        schedules['schedules'][id_] = data
        write_schedules(schedules)
        scheduler_update(scheduler=self.scheduler, id_=id_)
        self.write(get_schedule(id_=id_))

    @gen.coroutine
    def delete(self, id_=None, *args, **kwargs):
        if id_ is None:
            return self.build_error(message='an ID must be specified')
        schedules = read_schedules()
        if id_ in schedules.get('schedules', {}):
            del schedules['schedules'][id_]
            write_schedules(schedules)
        scheduler_delete(scheduler=self.scheduler, id_=id_)
        self.build_success(message='removed schedule %s' % id_)


class ResetSchedulesHandler(BaseHandler):
    @gen.coroutine
    def post(self, *args, **kwargs):
        reset_from_schedules(self.scheduler)


class HistoryHandler(BaseHandler):
    @gen.coroutine
    def get(self, id_, *args, **kwargs):
        self.write({'history': get_history(id_)})


class TemplateHandler(BaseHandler):
    """Handler for the / path."""
    app_path = os.path.join(os.path.dirname(__file__), "dist")

    @gen.coroutine
    def get(self, *args, **kwargs):
        page = 'index.html'
        if args and args[0]:
            page = args[0].strip('/')
        arguments = self.arguments
        self.render(page, **arguments)


def serve():
    jobstores = {'default': SQLAlchemyJobStore(url=JOBS_STORE)}
    scheduler = TornadoScheduler(jobstores=jobstores)
    scheduler.start()

    define('port', default=3210, help='run on the given port', type=int)
    define('address', default='', help='bind the server at the given address', type=str)
    define('ssl_cert', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_cert.pem'),
            help='specify the SSL certificate to use for secure connections')
    define('ssl_key', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_key.pem'),
            help='specify the SSL private key to use for secure connections')
    define('debug', default=False, help='run in debug mode')
    define('config', help='read configuration file',
            callback=lambda path: tornado.options.parse_config_file(path, final=False))
    tornado.options.parse_command_line()

    if options.debug:
        logger.setLevel(logging.DEBUG)

    ssl_options = {}
    if os.path.isfile(options.ssl_key) and os.path.isfile(options.ssl_cert):
        ssl_options = dict(certfile=options.ssl_cert, keyfile=options.ssl_key)

    init_params = dict(listen_port=options.port, logger=logger, ssl_options=ssl_options,
                       scheduler=scheduler)

    _reset_schedules_path = r'schedules/reset'
    _schedules_path = r'schedules/?(?P<id_>\d+)?'
    _history_path = r'history/?(?P<id_>\d+)'
    application = tornado.web.Application([
            ('/api/%s' % _reset_schedules_path, ResetSchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _reset_schedules_path), ResetSchedulesHandler, init_params),
            ('/api/%s' % _schedules_path, SchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _schedules_path), SchedulesHandler, init_params),
            ('/api/%s' % _history_path, HistoryHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _history_path), HistoryHandler, init_params),
            (r'/?(.*)', TemplateHandler, init_params),
        ],
        static_path=os.path.join(os.path.dirname(__file__), 'dist/static'),
        template_path=os.path.join(os.path.dirname(__file__), 'dist/'),
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
