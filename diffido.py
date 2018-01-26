#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diffido - because the F5 key is a terrible thing to waste.

Copyright 2018 Davide Alberani <da@erlug.linux.it>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import os
import re
import io
import json
import shutil
import urllib
import smtplib
from email.mime.text import MIMEText
import logging
import datetime
import requests
import subprocess
import multiprocessing
from lxml import etree
from xml.etree import ElementTree

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
DEFAULT_CONF = 'conf/diffido.conf'
EMAIL_FROM = 'diffido@localhost'
GIT_CMD = 'git'

re_commit = re.compile(r'^(?P<id>[0-9a-f]{40}) (?P<message>.*)\n(?: .* '
                       '(?P<insertions>\d+) insertion.* (?P<deletions>\d+) deletion.*$)?', re.M)
re_insertion = re.compile(r'(\d+) insertion')
re_deletion = re.compile(r'(\d+) deletion')

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def read_schedules():
    """Return the schedules configuration.

    :returns: dictionary from the JSON object in conf/schedules.json
    :rtype: dict"""
    if not os.path.isfile(SCHEDULES_FILE):
        return {'schedules': {}}
    try:
        with open(SCHEDULES_FILE, 'r') as fd:
            schedules = json.loads(fd.read())
            for id_ in schedules.get('schedules', {}).keys():
                schedule = schedules['schedules'][id_]
                try:
                    schedule['last_history'] = get_last_history(id_)
                except:
                    schedule['last_history'] = {}
                    continue
            return schedules
    except Exception as e:
        logger.error('unable to read %s: %s' % (SCHEDULES_FILE, e))
        return {'schedules': {}}


def write_schedules(schedules):
    """Write the schedules configuration.

    :param schedules: the schedules to save
    :type schedules: dict
    :returns: True in case of success
    :rtype: bool"""
    try:
        with open(SCHEDULES_FILE, 'w') as fd:
            fd.write(json.dumps(schedules, indent=2))
    except Exception as e:
        logger.error('unable to write %s: %s' % (SCHEDULES_FILE, e))
        return False
    return True


def next_id(schedules):
    """Return the next available integer (as a string) in the list of schedules keys (do not fills holes)

    :param schedules: the schedules
    :type schedules: dict
    :returns: the ID of the next schedule
    :rtype: str"""
    ids = schedules.get('schedules', {}).keys()
    if not ids:
        return '1'
    return str(max([int(i) for i in ids]) + 1)


def get_schedule(id_, add_id=True, add_history=False):
    """Return information about a single schedule

    :param id_: ID of the schedule
    :type id_: str
    :param add_id: if True, add the ID in the dictionary
    :type add_id: bool
    :returns: the schedule
    :rtype: dict"""
    try:
        schedules = read_schedules()
    except Exception:
        return {}
    data = schedules.get('schedules', {}).get(id_, {})
    if add_history and data:
        data['last_history'] = get_last_history(id_)
    if add_id:
        data['id'] = str(id_)
    return data


def select_xpath(content, xpath):
    """Select a portion of a HTML document

    :param content: the content of the document
    :type content: str
    :param xpath: the XPath selector
    :type xpath: str
    :returns: the selected document
    :rtype: str"""
    fd = io.StringIO(content)
    tree = etree.parse(fd)
    elems = tree.xpath(xpath)
    if not elems:
        return content
    selected_content = []
    for elem in elems:
        selected_content.append(''.join([elem.text] + [ElementTree.tostring(e).decode('utf-8', 'replace')
                                                    for e in elem.getchildren()]))
    content = ''.join(selected_content)
    return content


def run_job(id_=None, *args, **kwargs):
    """Run a job

    :param id_: ID of the schedule to run
    :type id_: str
    :param args: positional arguments
    :type args: tuple
    :param kwargs: named arguments
    :type kwargs: dict
    :returns: True in case of success
    :rtype: bool"""
    schedule = get_schedule(id_, add_id=False)
    url = schedule.get('url')
    if not url:
        return False
    logger.debug('running job id:%s title:%s url: %s' % (id_, schedule.get('title', ''), url))
    if not schedule.get('enabled'):
        logger.info('not running job %s: disabled' % id_)
        return True
    req = requests.get(url, allow_redirects=True, timeout=(30.10, 240))
    content = req.text
    xpath = schedule.get('xpath')
    if xpath:
        try:
            content = select_xpath(content, xpath)
        except Exception as e:
            logger.warn('unable to extract XPath %s: %s' % (xpath, e))
    req_path = urllib.parse.urlparse(req.url).path
    base_name = os.path.basename(req_path) or 'index.html'
    def _commit(id_, filename, content, queue):
        os.chdir('storage/%s' % id_)
        current_lines = 0
        if os.path.isfile(filename):
            with open(filename, 'r') as fd:
                for line in fd:
                    current_lines += 1
        with open(filename, 'w') as fd:
            fd.write(content)
        p = subprocess.Popen([GIT_CMD, 'add', filename])
        p.communicate()
        p = subprocess.Popen([GIT_CMD, 'commit', '-m', '%s' % datetime.datetime.utcnow(), '--allow-empty'],
                             stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        stdout = stdout.decode('utf-8')
        insert = re_insertion.findall(stdout)
        if insert:
            insert = int(insert[0])
        else:
            insert = 0
        delete = re_deletion.findall(stdout)
        if delete:
            delete = int(delete[0])
        else:
            delete = 0
        queue.put({'insertions': insert, 'deletions': delete, 'previous_lines': current_lines,
                   'changes': max(insert, delete)})
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_commit, args=(id_, base_name, content, queue))
    p.start()
    res = queue.get()
    p.join()
    email = schedule.get('email')
    if not email:
        return True
    changes = res.get('changes')
    if not changes:
        return True
    min_change = schedule.get('minimum_change')
    previous_lines = res.get('previous_lines')
    if min_change and previous_lines:
        min_change = float(min_change)
        change_fraction = res.get('changes') / previous_lines
        if change_fraction < min_change:
            return True
    # send notification
    diff = get_diff(id_).get('diff')
    if not diff:
        return True
    send_email(to=email, subject='%s page changed' % schedule.get('title'),
               body='changes:\n\n%s' % diff)
    return True


def safe_run_job(id_=None, *args, **kwargs):
    """Safely run a job, catching all the exceptions

    :param id_: ID of the schedule to run
    :type id_: str
    :param args: positional arguments
    :type args: tuple
    :param kwargs: named arguments
    :type kwargs: dict
    :returns: True in case of success
    :rtype: bool"""
    try:
        run_job(id_, *args, **kwargs)
    except Exception as e:
        send_email('error executing job %s: %s' % (id_, e))


def send_email(to, subject='diffido', body='', from_=None):
    """Send an email

    :param to: destination address
    :type to: str
    :param subject: email subject
    :type subject: str
    :param body: body of the email
    :type body: str
    :param from_: sender address
    :type from_: str
    :returns: True in case of success
    :rtype: bool"""
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_ or EMAIL_FROM
    msg['To'] = to
    with smtplib.SMTP('localhost') as s:
        s.send_message(msg)
    return True


def get_history(id_, limit=None, add_info=False):
    """Read the history of a schedule

    :param id_: ID of the schedule
    :type id_: str
    :param limit: number of entries to fetch
    :type limit: int
    :param add_info: add information about the schedule itself
    :type add_info: int
    :returns: information about the schedule and its history
    :rtype: dict"""
    def _history(id_, limit, queue):
        os.chdir('storage/%s' % id_)
        cmd = [GIT_CMD, 'log', '--pretty=oneline', '--shortstat']
        if limit is not None:
            cmd.append('-%s' % limit)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        queue.put(stdout)
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_history, args=(id_, limit, queue))
    p.start()
    res = queue.get().decode('utf-8')
    p.join()
    history = []
    for match in re_commit.finditer(res):
        info = match.groupdict()
        info['insertions'] = int(info['insertions'] or 0)
        info['deletions'] = int(info['deletions'] or 0)
        info['changes'] = max(info['insertions'], info['deletions'])
        history.append(info)
    last_id = None
    if history and 'id' in history[0]:
        last_id = history[0]['id']
    for idx, item in enumerate(history):
        item['seq'] = idx + 1
    data = {'history': history, 'last_id': last_id}
    if add_info:
        data['schedule'] = get_schedule(id_)
    return data


def get_last_history(id_):
    """Read the last history entry of a schedule

    :param id_: ID of the schedule
    :type id_: str
    :returns: information about the schedule and its history
    :rtype: dict"""
    history = get_history(id_, limit=1)
    return history.get('history', [{}])[0]


def get_diff(id_, commit_id='HEAD', old_commit_id=None):
    """Return the diff between commits of a schedule

    :param id_: ID of the schedule
    :type id_: str
    :param commit_id: the most recent commit ID; HEAD by default
    :type commit_id: str
    :param old_commit_id: the older commit ID; if None, the previous commit is used
    :type old_commit_id: str
    :returns: information about the schedule and the diff between commits
    :rtype: dict"""
    def _history(id_, commit_id, old_commit_id, queue):
        os.chdir('storage/%s' % id_)
        p = subprocess.Popen([GIT_CMD, 'diff', old_commit_id or '%s~' % commit_id, commit_id],
                             stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        queue.put(stdout)
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_history, args=(id_, commit_id, old_commit_id, queue))
    p.start()
    res = queue.get().decode('utf-8')
    p.join()
    schedule = get_schedule(id_)
    return {'diff': res, 'schedule': schedule}


def scheduler_update(scheduler, id_):
    """Update a scheduler job, using information from the JSON object

    :param scheduler: the TornadoScheduler instance to modify
    :type scheduler: TornadoScheduler
    :param id_: ID of the schedule that must be updated
    :type id_: str
    :returns: True in case of success
    :rtype: bool"""
    schedule = get_schedule(id_, add_id=False)
    if not schedule:
        logger.warn('unable to update empty schedule %s' % id_)
        return False
    trigger = schedule.get('trigger')
    if trigger not in ('interval', 'cron'):
        logger.warn('unable to update empty schedule %s: trigger not in ("cron", "interval")' % id_)
        return False
    args = {}
    if trigger == 'interval':
        args['trigger'] = 'interval'
        for unit in 'weeks', 'days', 'hours', 'minutes', 'seconds':
            if 'interval_%s' % unit not in schedule:
                continue
            try:
                args[unit] = int(schedule['interval_%s' % unit])
            except Exception:
                logger.warn('invalid argument on schedule %s: %s parameter %s is not an integer' %
                            (id_, 'interval_%s' % unit, schedule['interval_%s' % unit]))
    elif trigger == 'cron':
        try:
            cron_trigger = CronTrigger.from_crontab(schedule['cron_crontab'])
            args['trigger'] = cron_trigger
        except Exception:
            logger.warn('invalid argument on schedule %s: cron_tab parameter %s is not a valid crontab' %
                        (id_, schedule.get('cron_crontab')))
    git_create_repo(id_)
    try:
        scheduler.add_job(safe_run_job, id=id_, replace_existing=True, kwargs={'id_': id_}, **args)
    except Exception as e:
        logger.warn('unable to update job %s: %s' % (id_, e))
        return False
    return True


def scheduler_delete(scheduler, id_):
    """Update a scheduler job, using information from the JSON object

    :param scheduler: the TornadoScheduler instance to modify
    :type scheduler: TornadoScheduler
    :param id_: ID of the schedule
    :type id_: str
    :returns: True in case of success
    :rtype: bool"""
    try:
        scheduler.remove_job(job_id=id_)
    except Exception as e:
        logger.warn('unable to delete job %s: %s' % (id_, e))
        return False
    return git_delete_repo(id_)


def reset_from_schedules(scheduler):
    """"Reset all scheduler jobs, using information from the JSON object

    :param scheduler: the TornadoScheduler instance to modify
    :type scheduler: TornadoScheduler
    :returns: True in case of success
    :rtype: bool"""
    ret = False
    try:
        scheduler.remove_all_jobs()
        for key in read_schedules().get('schedules', {}).keys():
            ret |= scheduler_update(scheduler, id_=key)
    except Exception as e:
        logger.warn('unable to reset all jobs: %s' % e)
        return False
    return ret


def git_create_repo(id_):
    """Create a Git repository

    :param id_: ID of the schedule
    :type id_: str
    :returns: True in case of success
    :rtype: bool"""
    repo_dir = 'storage/%s' % id_
    if os.path.isdir(repo_dir):
        return True
    p = subprocess.Popen([GIT_CMD, 'init', repo_dir])
    p.communicate()
    return p.returncode == 0


def git_delete_repo(id_):
    """Delete a Git repository

    :param id_: ID of the schedule
    :type id_: str
    :returns: True in case of success
    :rtype: bool"""
    repo_dir = 'storage/%s' % id_
    if not os.path.isdir(repo_dir):
        return False
    try:
        shutil.rmtree(repo_dir)
    except Exception as e:
        logger.warn('unable to delete Git repository %s: %s' % (id_, e))
        return False
    return True


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
    """Schedules handler."""
    @gen.coroutine
    def get(self, id_=None, *args, **kwargs):
        """Get a schedule."""
        if id_ is not None:
            return self.write({'schedule': get_schedule(id_, add_history=True)})
        schedules = read_schedules()
        self.write(schedules)

    @gen.coroutine
    def put(self, id_=None, *args, **kwargs):
        """Update a schedule."""
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
        """Add a schedule."""
        data = self.clean_body
        schedules = read_schedules()
        id_ = next_id(schedules)
        schedules['schedules'][id_] = data
        write_schedules(schedules)
        scheduler_update(scheduler=self.scheduler, id_=id_)
        self.write(get_schedule(id_=id_))

    @gen.coroutine
    def delete(self, id_=None, *args, **kwargs):
        """Delete a schedule."""
        if id_ is None:
            return self.build_error(message='an ID must be specified')
        schedules = read_schedules()
        if id_ in schedules.get('schedules', {}):
            del schedules['schedules'][id_]
            write_schedules(schedules)
        scheduler_delete(scheduler=self.scheduler, id_=id_)
        self.build_success(message='removed schedule %s' % id_)


class RunScheduleHandler(BaseHandler):
    """Reset schedules handler."""
    @gen.coroutine
    def post(self, id_, *args, **kwargs):
        if run_job(id_):
            return self.build_success('job run')
        self.build_error('job not run')


class ResetSchedulesHandler(BaseHandler):
    """Reset schedules handler."""
    @gen.coroutine
    def post(self, *args, **kwargs):
        reset_from_schedules(self.scheduler)


class HistoryHandler(BaseHandler):
    """History handler."""
    @gen.coroutine
    def get(self, id_, *args, **kwargs):
        self.write(get_history(id_, add_info=True))


class DiffHandler(BaseHandler):
    """Diff handler."""
    @gen.coroutine
    def get(self, id_, commit_id, old_commit_id=None, *args, **kwargs):
        self.write(get_diff(id_, commit_id, old_commit_id))


class TemplateHandler(BaseHandler):
    """Handler for the template files in the / path."""
    @gen.coroutine
    def get(self, *args, **kwargs):
        """Get a template file."""
        page = 'index.html'
        if args and args[0]:
            page = args[0].strip('/')
        arguments = self.arguments
        self.render(page, **arguments)


def serve():
    """Read configuration and start the server."""
    global EMAIL_FROM
    jobstores = {'default': SQLAlchemyJobStore(url=JOBS_STORE)}
    scheduler = TornadoScheduler(jobstores=jobstores)
    scheduler.start()

    define('port', default=3210, help='run on the given port', type=int)
    define('address', default='', help='bind the server at the given address', type=str)
    define('ssl_cert', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_cert.pem'),
            help='specify the SSL certificate to use for secure connections')
    define('ssl_key', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_key.pem'),
            help='specify the SSL private key to use for secure connections')
    define('admin-email', default='', help='email address of the site administrator', type=str)
    define('debug', default=False, help='run in debug mode')
    define('config', help='read configuration file',
            callback=lambda path: tornado.options.parse_config_file(path, final=False))
    if not options.config and os.path.isfile(DEFAULT_CONF):
        tornado.options.parse_config_file(DEFAULT_CONF, final=False)
    tornado.options.parse_command_line()
    if options.admin_email:
        EMAIL_FROM = options.admin_email

    if options.debug:
        logger.setLevel(logging.DEBUG)

    ssl_options = {}
    if os.path.isfile(options.ssl_key) and os.path.isfile(options.ssl_cert):
        ssl_options = dict(certfile=options.ssl_cert, keyfile=options.ssl_key)

    init_params = dict(listen_port=options.port, logger=logger, ssl_options=ssl_options,
                       scheduler=scheduler)

    _reset_schedules_path = r'schedules/reset'
    _schedule_run_path = r'schedules/(?P<id_>\d+)/run'
    _schedules_path = r'schedules/?(?P<id_>\d+)?'
    _history_path = r'history/?(?P<id_>\d+)'
    _diff_path = r'diff/(?P<id_>\d+)/(?P<commit_id>[0-9a-f]+)/?(?P<old_commit_id>[0-9a-f]+)?/?'
    application = tornado.web.Application([
            (r'/api/%s' % _reset_schedules_path, ResetSchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _reset_schedules_path), ResetSchedulesHandler, init_params),
            (r'/api/%s' % _schedule_run_path, RunScheduleHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _schedule_run_path), RunScheduleHandler, init_params),
            (r'/api/%s' % _schedules_path, SchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _schedules_path), SchedulesHandler, init_params),
            (r'/api/%s' % _history_path, HistoryHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _history_path), HistoryHandler, init_params),
            (r'/api/%s' % _diff_path, DiffHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _diff_path), DiffHandler, init_params),
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
