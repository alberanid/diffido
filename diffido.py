#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diffido - because the F5 key is a terrible thing to waste.

Copyright 2018-2026 Davide Alberani <da@mimante.net>

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
import json
import pytz
import shutil
import smtplib
from string import Template
from urllib.parse import urlparse
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.encoders import encode_base64
from email.utils import formatdate
import logging
import datetime
import requests
import subprocess
import time
import multiprocessing
from lxml import etree
from xml.etree import ElementTree

from tornado.ioloop import IOLoop
from tornado import web
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
VERSION = '5.0'
API_VERSION = '1.0'
PROJECT_URL = 'https://github.com/alberanid/diffido'
SCHEDULES_FILE = 'conf/schedules.json'
DEFAULT_CONF = 'conf/diffido.conf'
EMAIL_FROM = 'diffido@localhost'
DEFAULT_EMAIL_TEMPLATE = 'conf/email_template.txt'
DEFAULT_EMAIL_ERROR_TEMPLATE = 'conf/email_error_template.txt'
EMAIL_TEMPLATE = DEFAULT_EMAIL_TEMPLATE
EMAIL_ERROR_TEMPLATE = DEFAULT_EMAIL_ERROR_TEMPLATE
ERROR_STATE_FILE = 'conf/error_state.json'
ERROR_EMAIL_INTERVAL = 24 * 60 * 60
EMAIL_SUBJECT_PREFIX = 'Subject:'
SMTP_SETTINGS = {}
GIT_CMD = 'git'
# Maximum number of diff lines sent to clients (diff page, notification emails).
# Large diffs are truncated and flagged, to keep pages and payloads fast.
MAX_DIFF_LINES = 1000

# Fallback content of the email templates, used when the files are missing
# or unreadable; the first line is the subject of the email, when prefixed
# with EMAIL_SUBJECT_PREFIX.
CHANGE_EMAIL_TEMPLATE = """\
Subject: $title page changed
Schedule: $id$title_suffix
URL: $url
Change: $insertions insertion(s), $deletions deletion(s) out of $previous_lines previous line(s)
$previous_revision_line$current_revision_line$date_line$xpath_line$minimum_change_line
The unified diff is attached to this email.
"""
ERROR_EMAIL_TEMPLATE = """\
Subject: diffido job error
error executing job $id$url_suffix: $error
"""

re_commit = re.compile(
    r'^(?P<id>[0-9a-f]{40}) (?P<message>.*)\n(?: (?P<stat>[^\n]*)\n)?',
    re.M,
)
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
                try:
                    schedule['last_change'] = get_last_change(id_)
                except:
                    schedule['last_change'] = {}
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
    tree = etree.HTML(content)
    elems = tree.xpath(xpath)
    if not elems:
        return content
    selected_content = []
    for elem in elems:
        pieces = []
        if elem.text:
            pieces.append(elem.text)
        for sub_el in list(elem):
            try:
                sub_el_text = ElementTree.tostring(sub_el, method='html').decode('utf-8', 'replace')
            except Exception:
                continue
            if sub_el_text:
                pieces.append(sub_el_text)
        selected_content.append(''.join(pieces))
    content = ''.join(selected_content).strip()
    return content


def user_agent():
    """Return the User-Agent header value for outgoing HTTP/HTTPS requests.

    The value is read from the `user_agent` option, defined in the
    configuration file (conf/diffido.conf, see `DEFAULT_CONF`), and should
    follow the Wikimedia Foundation User-Agent Policy
    (https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy),
    i.e. `<client name>/<version> (<contact information>) <library>/<version>`.

    :returns: the User-Agent header value
    :rtype: str"""
    return getattr(options, 'user_agent', '')


def _commit_job(id_, filename, content, queue):
    """Store the fetched content and commit it.

    Run in a separate process: it changes the working directory of the
    process, so it must not run in the server process.  Defined at module
    level to be picklable by every multiprocessing start method.

    :param id_: ID of the schedule
    :type id_: str
    :param filename: name of the file to write
    :type filename: str
    :param content: content to write in the file
    :type content: str
    :param queue: queue used to send back the result
    :type queue: multiprocessing.Queue"""
    try:
        os.chdir('storage/%s' % id_)
    except Exception as e:
        logger.info('unable to move to storage/%s directory: %s; trying to create it...' % (id_, e))
        _created = False
        try:
            _created = git_create_repo(id_)
        except Exception as e:
            logger.info('unable to move to storage/%s directory: %s; unable to create it' % (id_, e))
        if not _created:
            return queue.put({})
    current_lines = 0
    if os.path.isfile(filename):
        with open(filename, 'r') as fd:
            for line in fd:
                current_lines += 1
    with open(filename, 'w') as fd:
        fd.write(content)
    p = subprocess.Popen([GIT_CMD, 'add', filename])
    p.communicate()
    p = subprocess.Popen([GIT_CMD, 'commit', '-m', '%s' % datetime.datetime.now(datetime.timezone.utc), '--allow-empty'],
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


def run_job(id_=None, force=False, *args, **kwargs):
    """Run a job

    :param id_: ID of the schedule to run
    :type id_: str
    :param force: run even if disabled
    :type force: bool
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
    if not schedule.get('enabled') and not force:
        logger.info('not running job %s: disabled' % id_)
        return True
    req = requests.get(url, headers={'User-Agent': user_agent()},
                       allow_redirects=True, timeout=(30.10, 240))
    content = req.text
    xpath = schedule.get('xpath')
    if xpath:
        try:
            content = select_xpath(content, xpath)
        except Exception as e:
            logger.warning('unable to extract XPath %s: %s' % (xpath, e))
    req_path = urlparse(req.url).path
    base_name = os.path.basename(req_path) or 'index.html'
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_commit_job, args=(id_, base_name, content, queue))
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
    history = get_history(id_, limit=2).get('history') or []
    revision = history[0] if history else {}
    old_revision = history[1] if len(history) > 1 else {}
    subject, body = build_change_email(id_, schedule, res, revision, old_revision)
    attachment_name = 'diff-%s.diff' % revision.get('id', 'HEAD')[:12]
    attachments = [(attachment_name, diff, 'text/x-patch')]
    send_email(to=email, subject=subject, body=body, attachments=attachments)
    return True


def read_email_template(path, default):
    """Return the content of an email template file.

    The `default` template is returned when the file can not be read, so
    that a missing or unreadable template never prevents an email from
    being sent.

    :param path: path of the template file
    :type path: str
    :param default: template used as a fallback
    :type default: str
    :returns: the content of the template
    :rtype: str"""
    try:
        with open(path, 'r') as fd:
            return fd.read()
    except Exception as e:
        logger.warning('unable to read email template %s: %s' % (path, e))
        return default


def render_email_template(template, context, default_subject='diffido'):
    """Render an email template.

    The first line of the template, when it starts with `Subject:`, is used
    as the subject of the email; all the remaining lines are the body.
    Placeholders are expanded with `string.Template` syntax; unknown
    placeholders are left untouched.

    :param template: the template
    :type template: str
    :param context: values used to expand the placeholders
    :type context: dict
    :param default_subject: subject used when the template has no
                            `Subject:` line
    :type default_subject: str
    :returns: tuple with the subject and the body of the email
    :rtype: tuple"""
    subject = default_subject
    body = template
    first_line, sep, rest = template.partition('\n')
    if first_line.startswith(EMAIL_SUBJECT_PREFIX):
        subject = first_line[len(EMAIL_SUBJECT_PREFIX):].strip()
        body = rest if sep else ''
    subject = Template(subject).safe_substitute(context)
    body = Template(body).safe_substitute(context).rstrip('\n')
    return subject, body


def build_change_email(id_, schedule, result, revision=None, old_revision=None):
    """Build subject and body of the notification email sent when a page changed.

    Subject and body are built from the template read from the
    `email_template` option (see `EMAIL_TEMPLATE`).

    :param id_: ID of the schedule
    :type id_: str
    :param schedule: the schedule that detected the change
    :type schedule: dict
    :param result: dictionary with the number of insertions, deletions,
                   changes and previous lines
    :type result: dict
    :param revision: the current history entry of the schedule
    :type revision: dict
    :param old_revision: the previous history entry of the schedule
    :type old_revision: dict
    :returns: tuple with the subject and the body of the email
    :rtype: tuple"""
    revision = revision or {}
    old_revision = old_revision or {}
    title = schedule.get('title')
    context = {
        'id': id_,
        'title': title or 'diffido',
        'title_suffix': ' - %s' % title if title else '',
        'url': schedule.get('url') or '',
        'insertions': result.get('insertions', 0),
        'deletions': result.get('deletions', 0),
        'changes': result.get('changes', 0),
        'previous_lines': result.get('previous_lines', 0),
        'previous_revision': old_revision.get('id') or '',
        'current_revision': revision.get('id') or '',
        'date': revision.get('message') or '',
        'xpath': schedule.get('xpath') or '',
        'minimum_change': schedule.get('minimum_change') or '',
    }
    for key, label in (('previous_revision', 'Previous revision'),
                       ('current_revision', 'Current revision'),
                       ('date', 'Date'), ('xpath', 'XPath selector'),
                       ('minimum_change', 'Minimum change')):
        # Whole lines, available only when the related value is set.
        context['%s_line' % key] = '%s: %s\n' % (label, context[key]) if context[key] else ''
    template = read_email_template(EMAIL_TEMPLATE, CHANGE_EMAIL_TEMPLATE)
    return render_email_template(template, context, '$title page changed')


def read_error_state():
    """Return the state of the error notifications already sent.

    :returns: dictionary mapping schedule IDs to their last error notice
    :rtype: dict"""
    if not os.path.isfile(ERROR_STATE_FILE):
        return {}
    try:
        with open(ERROR_STATE_FILE, 'r') as fd:
            return json.loads(fd.read())
    except Exception as e:
        logger.error('unable to read %s: %s' % (ERROR_STATE_FILE, e))
        return {}


def write_error_state(state):
    """Save the state of the error notifications already sent.

    :param state: dictionary mapping schedule IDs to their last error notice
    :type state: dict
    :returns: True in case of success
    :rtype: bool"""
    try:
        with open(ERROR_STATE_FILE, 'w') as fd:
            fd.write(json.dumps(state, indent=2))
    except Exception as e:
        logger.error('unable to write %s: %s' % (ERROR_STATE_FILE, e))
        return False
    return True


def reset_error_state(id_):
    """Forget the last error notice sent for a schedule.

    Called when a job runs successfully, so that a later failure is
    notified again even if identical to a previous one.

    :param id_: ID of the schedule
    :type id_: str"""
    state = read_error_state()
    if id_ not in state:
        return
    del state[id_]
    write_error_state(state)


def should_notify_error(id_, error):
    """Record an error and decide whether an email must be sent.

    A notice is sent when the error is different from the last one
    notified for the schedule, or when the same error persists for more
    than ERROR_EMAIL_INTERVAL seconds; identical, recent errors are
    silently skipped to avoid flooding the administrator.

    :param id_: ID of the schedule
    :type id_: str
    :param error: signature of the error (type and message)
    :type error: str
    :returns: True when an error email must be sent
    :rtype: bool"""
    state = read_error_state()
    now = time.time()
    previous = state.get(id_)
    if previous is not None and previous.get('error') == error and \
            (not ERROR_EMAIL_INTERVAL or now - previous.get('last', 0) < ERROR_EMAIL_INTERVAL):
        previous['count'] = previous.get('count', 1) + 1
        state[id_] = previous
        write_error_state(state)
        logger.info('skipping error email for job %s: same error already notified (%d times)' %
                    (id_, previous['count']))
        return False
    state[id_] = {'error': error, 'count': 1, 'first': now, 'last': now}
    write_error_state(state)
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
        res = run_job(id_, *args, **kwargs)
    except Exception as e:
        error = '%s: %s' % (type(e).__name__, e)
        if not should_notify_error(id_, error):
            return False
        recipient = SMTP_SETTINGS.get('smtp-username') or EMAIL_FROM
        schedule = get_schedule(id_, add_id=False)
        url = schedule.get('url') or ''
        context = {'id': id_, 'url': url, 'url_suffix': ' (%s)' % url if url else '',
                   'error': error}
        template = read_email_template(EMAIL_ERROR_TEMPLATE, ERROR_EMAIL_TEMPLATE)
        subject, body = render_email_template(template, context, 'diffido job error')
        send_email(to=recipient, subject=subject, body=body)
        return False
    reset_error_state(id_)
    return res


def send_email(to, subject='diffido', body='', from_=None, attachments=None):
    """Send an email

    :param to: destination address
    :type to: str
    :param subject: email subject
    :type subject: str
    :param body: body of the email
    :type body: str
    :param from_: sender address
    :type from_: str
    :param attachments: sequence of (filename, content, mimetype) tuples
    :type attachments: list
    :returns: True in case of success
    :rtype: bool"""
    if attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body))
        for filename, content, mimetype in attachments:
            maintype, _, subtype = mimetype.partition('/')
            part = MIMEBase(maintype, subtype or 'octet-stream')
            part.set_payload(content.encode('utf-8') if isinstance(content, str) else content)
            encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(part)
    else:
        msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = from_ or EMAIL_FROM
    msg['To'] = to
    msg["Date"] = formatdate(localtime=True)
    starttls = SMTP_SETTINGS.get('smtp-starttls')
    use_ssl = SMTP_SETTINGS.get('smtp-use-ssl')
    username = SMTP_SETTINGS.get('smtp-username')
    password = SMTP_SETTINGS.get('smtp-password')
    args = {}
    for key, value in SMTP_SETTINGS.items():
        if key in ('smtp-starttls', 'smtp-use-ssl', 'smtp-username', 'smtp-password'):
            continue
        if key in ('smtp-port'):
            value = int(value)
        key = key.replace('smtp-', '', 1).replace('-', '_')
        args[key] = value
    try:
        if use_ssl:
            for key in ('ssl_keyfile', 'ssl_certfile', 'ssl_context'):
                if key in args:
                    args[key.replace('ssl_', '')] = args[key]
                    del args[key]
            logger.debug('STMP SSL connection with args: %s' % repr(args))
            with smtplib.SMTP_SSL(**args) as s:
                if username:
                    logger.debug('STMP LOGIN for username %s and password of length %d' % (username, len(password)))
                    s.login(username, password)
                s.send_message(msg)
        else:
            tls_args = {}
            for key in ('ssl_keyfile', 'ssl_certfile', 'ssl_context'):
                if key in args:
                    tls_args[key.replace('ssl_', '')] = args[key]
                    del args[key]
            logger.debug('STMP connection with args: %s' % repr(args))
            with smtplib.SMTP(**args) as s:
                if starttls:
                    logger.debug('STMP STARTTLS connection with args: %s' % repr(tls_args))
                    s.ehlo_or_helo_if_needed()
                    s.starttls(**tls_args)
                if username:
                    logger.debug('STMP LOGIN for username %s and password of length %d' % (username, len(password)))
                    s.login(username, password)
                s.send_message(msg)
    except Exception as e:
        logger.error('unable to send email to %s: %s' % (to, e))
        return False
    return True


def _run_git_in_dir(id_, cmd, queue):
    """Run a git command inside the repository of a schedule.

    Run in a separate process: it changes the working directory of the
    process, so it must not run in the server process.  Defined at module
    level to be picklable by every multiprocessing start method.

    :param id_: ID of the schedule
    :type id_: str
    :param cmd: the git command to run
    :type cmd: list
    :param queue: queue used to send back a (returncode, stdout, stderr) tuple
    :type queue: multiprocessing.Queue"""
    try:
        os.chdir('storage/%s' % id_)
    except Exception as e:
        logger.info('unable to move to storage/%s directory: %s' % (id_, e))
        return queue.put((1, b'', b''))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    queue.put((p.returncode, stdout, stderr))


def get_history(id_, limit=None, offset=0, add_info=False, show_empty=True):
    """Read the history of a schedule

    :param id_: ID of the schedule
    :type id_: str
    :param limit: number of entries to fetch
    :type limit: int
    :param offset: number of entries to skip (used for pagination)
    :type offset: int
    :param add_info: add information about the schedule itself
    :type add_info: int
    :param show_empty: if False, exclude entries with no changes
    :type show_empty: bool
    :returns: information about the schedule and its history
    :rtype: dict"""
    cmd = [GIT_CMD, 'log', '--pretty=oneline', '--shortstat']
    # When entries with no changes are filtered out, git can't skip them
    # while paginating, so the whole history is read and sliced afterwards.
    if show_empty:
        if limit is not None:
            cmd.append('-%s' % limit)
        if offset:
            cmd.extend(['--skip', str(offset)])
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_run_git_in_dir, args=(id_, cmd, queue))
    p.start()
    returncode, res, _ = queue.get()
    p.join()
    res = res.decode('utf-8')
    history = []
    for match in re_commit.finditer(res):
        info = match.groupdict()
        stat = info.pop('stat', None) or ''
        insertions = re_insertion.findall(stat)
        deletions = re_deletion.findall(stat)
        info['insertions'] = int(insertions[0]) if insertions else 0
        info['deletions'] = int(deletions[0]) if deletions else 0
        info['changes'] = max(info['insertions'], info['deletions'])
        history.append(info)
    if not show_empty:
        history = [item for item in history if item.get('changes')]
    total = len(history)
    if show_empty:
        count_queue = multiprocessing.Queue()
        p = multiprocessing.Process(target=_run_git_in_dir, args=(id_, [GIT_CMD, 'rev-list', '--count', 'HEAD'], count_queue))
        p.start()
        try:
            _rc, count_output, _err = count_queue.get()
            total = int(count_output.decode('utf-8').strip() or 0)
        except (ValueError, UnicodeDecodeError):
            total = 0
        p.join()
    else:
        start = int(offset or 0)
        history = history[start:start + limit] if limit is not None else history[start:]
    last_id = None
    if history and 'id' in history[0]:
        last_id = history[0]['id']
    for idx, item in enumerate(history):
        item['seq'] = idx + 1 + int(offset or 0)
    data = {'history': history, 'last_id': last_id, 'total': total}
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
    hist = history.get('history') or [{}]
    return hist[0]


def get_last_change(id_):
    """Read the most recent history entry of a schedule that detected a change

    :param id_: ID of the schedule
    :type id_: str
    :returns: information about the most recent entry with changes
    :rtype: dict"""
    history = get_history(id_)
    for item in history.get('history') or []:
        if item.get('changes'):
            return item
    return {}


def get_diff(id_, commit_id='HEAD', old_commit_id=None):
    """Return the diff between commits of a schedule

    :param id_: ID of the schedule
    :type id_: str
    :param commit_id: the most recent commit ID; HEAD by default
    :type commit_id: str
    :param old_commit_id: the older commit ID; if None, the previous commit is used
    :type old_commit_id: str
    :returns: information about the schedule and the diff between commits; if the
              diff is longer than MAX_DIFF_LINES lines, it is truncated and the
              ``truncated`` flag is set (with ``total_lines`` and ``shown_lines``)
    :rtype: dict"""
    cmd = [GIT_CMD, 'diff', old_commit_id or '%s~' % commit_id, commit_id]
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_run_git_in_dir, args=(id_, cmd, queue))
    p.start()
    returncode, res, stderr = queue.get()
    p.join()
    schedule = get_schedule(id_)
    if returncode != 0:
        message = stderr.decode('utf-8', 'replace').strip() or 'git diff exited with code %s' % returncode
        logger.warning('unable to get diff of %s for schedule %s: %s' % (commit_id, id_, message))
        return {'diff': '', 'error': message, 'schedule': schedule}
    lines = res.decode('utf-8', 'replace').splitlines()
    truncated = len(lines) > MAX_DIFF_LINES
    data = {'diff': '\n'.join(lines[:MAX_DIFF_LINES]), 'schedule': schedule}
    if truncated:
        data.update(truncated=True, total_lines=len(lines), shown_lines=MAX_DIFF_LINES)
    return data


def _read_revision(id_, commit_id, queue):
    """Read the files stored at a given revision of a schedule.

    Run in a separate process: it changes the working directory of the
    process, so it must not run in the server process.  Defined at module
    level to be picklable by every multiprocessing start method.

    :param id_: ID of the schedule
    :type id_: str
    :param commit_id: the revision to read
    :type commit_id: str
    :param queue: queue used to send back a ('ok', files) or ('error', '') tuple
    :type queue: multiprocessing.Queue"""
    try:
        os.chdir('storage/%s' % id_)
    except Exception as e:
        logger.info('unable to move to storage/%s directory: %s' % (id_, e))
        return queue.put(('error', ''))
    p = subprocess.Popen([GIT_CMD, 'ls-tree', '-r', '--name-only', commit_id],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = p.communicate()
    if p.returncode != 0:
        logger.warning('unable to read revision %s of schedule %s: %s' % (commit_id, id_, stderr.decode('utf-8', 'replace').strip()))
        return queue.put(('error', ''))
    files = []
    for filename in stdout.decode('utf-8').splitlines():
        if not filename:
            continue
        p = subprocess.Popen([GIT_CMD, 'show', '%s:%s' % (commit_id, filename)],
                             stdout=subprocess.PIPE)
        content, _ = p.communicate()
        files.append({'name': filename,
                      'content': content.decode('utf-8', 'replace') if p.returncode == 0 else ''})
    queue.put(('ok', files))


def get_revision(id_, commit_id='HEAD'):
    """Return the page content stored at a given revision

    :param id_: ID of the schedule
    :type id_: str
    :param commit_id: the revision to show; HEAD by default
    :type commit_id: str
    :returns: information about the schedule and the content stored at the revision
    :rtype: dict"""
    commit_id = commit_id or 'HEAD'
    queue = multiprocessing.Queue()
    p = multiprocessing.Process(target=_read_revision, args=(id_, commit_id, queue))
    p.start()
    status, files = queue.get()
    p.join()
    schedule = get_schedule(id_)
    if status == 'error':
        return {'revision': {'id': commit_id, 'files': []}, 'error': 'Unknown revision "%s"' % commit_id, 'schedule': schedule}
    return {'revision': {'id': commit_id, 'files': files or []}, 'schedule': schedule}


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
        logger.warning('unable to update empty schedule %s' % id_)
        return False
    trigger = schedule.get('trigger')
    if trigger not in ('interval', 'cron'):
        logger.warning('unable to update empty schedule %s: trigger not in ("cron", "interval")' % id_)
        return False
    args = {}
    if trigger == 'interval':
        args['trigger'] = 'interval'
        for unit in 'weeks', 'days', 'hours', 'minutes', 'seconds':
            if 'interval_%s' % unit not in schedule:
                continue
            try:
                val = schedule['interval_%s' % unit]
                if not val:
                    continue
                args[unit] = int(val)
            except Exception:
                logger.warning('invalid argument on schedule %s: %s parameter %s is not an integer' %
                               (id_, 'interval_%s' % unit, schedule['interval_%s' % unit]))
        if len(args) == 1:
            logger.error('no valid interval specified, skipping schedule %s' % id_)
            return False
    elif trigger == 'cron':
        try:
            cron_trigger = CronTrigger.from_crontab(schedule['cron_crontab'])
            args['trigger'] = cron_trigger
        except Exception:
            logger.warning('invalid argument on schedule %s: cron_tab parameter %s is not a valid crontab' %
                           (id_, schedule.get('cron_crontab')))
            return False
    git_create_repo(id_)
    try:
        scheduler.add_job(safe_run_job, id=id_, replace_existing=True, kwargs={'id_': id_}, **args)
    except Exception as e:
        logger.warning('unable to update job %s: %s' % (id_, e))
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
        logger.warning('unable to delete job %s: %s' % (id_, e))
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
        logger.warning('unable to reset all jobs: %s' % e)
        return False
    return ret


def git_init():
    """Initialize Git global settings"""
    p = subprocess.Popen([GIT_CMD, 'config', '--global', 'user.email', EMAIL_FROM])
    p.communicate()
    p = subprocess.Popen([GIT_CMD, 'config', '--global', 'user.name', 'Diffido'])
    p.communicate()


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
        logger.warning('unable to delete Git repository %s: %s' % (id_, e))
        return False
    return True

DEFAULT_PAGE_SIZE = 20


def pagination_params(handler, default=DEFAULT_PAGE_SIZE):
    """Extract and validate page/page_size arguments from a request.

    :param handler: the request handler
    :type handler: tornado.web.RequestHandler
    :param default: default page size
    :type default: int
    :returns: (page, page_size) tuple, both positive integers
    :rtype: tuple"""
    def _int_arg(name, fallback):
        try:
            value = int(handler.get_query_argument(name, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(1, value)
    return _int_arg('page', 1), _int_arg('page_size', default)


def bool_arg(value, default=False):
    """Interpret a query string argument as a boolean.

    :param value: the raw query string value (None when missing)
    :type value: str
    :param default: value returned when the argument is missing
    :type default: bool
    :returns: the boolean value
    :rtype: bool"""
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def build_pagination(page, page_size, total):
    """Build a pagination metadata dictionary.

    :param page: current page number (1-based)
    :type page: int
    :param page_size: number of items per page
    :type page_size: int
    :param total: total number of items
    :type total: int
    :returns: pagination metadata
    :rtype: dict"""
    pages = (total + page_size - 1) // page_size if total else 0
    return {'page': page, 'page_size': page_size, 'total': total, 'pages': pages}


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
        """Get a schedule (or a paginated list of schedules)."""
        if id_ is not None:
            return self.write({'schedule': get_schedule(id_, add_history=True)})
        page, page_size = pagination_params(self)
        schedules = read_schedules().get('schedules', {})
        keys = sorted(schedules.keys())
        total = len(keys)
        start = (page - 1) * page_size
        selected = {key: schedules[key] for key in keys[start:start + page_size]}
        for key, value in selected.items():
            value['id'] = key
        self.write({'schedules': selected,
                    'pagination': build_pagination(page, page_size, total)})

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
    """Run a schedule handler."""
    @gen.coroutine
    def post(self, id_, *args, **kwargs):
        if run_job(id_, force=True):
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
        page, page_size = pagination_params(self)
        offset = (page - 1) * page_size
        show_empty = bool_arg(self.get_query_argument('show_empty', None), default=True)
        data = get_history(id_, limit=page_size, offset=offset, add_info=True, show_empty=show_empty)
        data['pagination'] = build_pagination(page, page_size, data.get('total', 0))
        self.write(data)

class DiffHandler(BaseHandler):
    """Diff handler."""
    @gen.coroutine
    def get(self, id_, commit_id, old_commit_id=None, *args, **kwargs):
        data = get_diff(id_, commit_id, old_commit_id)
        if data.get('error'):
            self.set_status(404)
            self.write({'message': 'Unknown revision "%s": %s' % (commit_id, data['error'])})
            return
        self.write(data)


class RevisionHandler(BaseHandler):
    """Revision handler."""
    @gen.coroutine
    def get(self, id_, commit_id='HEAD', *args, **kwargs):
        data = get_revision(id_, commit_id)
        if data.get('error'):
            self.set_status(404)
            self.write({'message': data['error']})
            return
        self.write(data)


class TemplateHandler(BaseHandler):
    """Handler for the template files in the / path."""
    @gen.coroutine
    def get(self, *args, **kwargs):
        """Get a template file."""
        page = 'index.html'
        if args and args[0]:
            page = args[0].strip('/')
        path = os.path.join(self.application.settings['template_path'], page)
        if not os.path.isfile(path):
            raise web.HTTPError(404)
        arguments = self.arguments
        arguments.setdefault('version', VERSION)
        self.render(page, **arguments)


def serve():
    """Read configuration and start the server."""
    global EMAIL_FROM, EMAIL_TEMPLATE, EMAIL_ERROR_TEMPLATE, SMTP_SETTINGS, ERROR_EMAIL_INTERVAL
    jobstores = {'default': SQLAlchemyJobStore(url=JOBS_STORE)}
    scheduler = TornadoScheduler(jobstores=jobstores, timezone=pytz.utc)
    scheduler.start()

    define('port', default=3210, help='run on the given port', type=int)
    define('address', default='', help='bind the server at the given address', type=str)
    define('ssl_cert', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_cert.pem'),
            help='specify the SSL certificate to use for secure connections')
    define('ssl_key', default=os.path.join(os.path.dirname(__file__), 'ssl', 'diffido_key.pem'),
            help='specify the SSL private key to use for secure connections')
    define('admin-email', default='', help='email address of the site administrator', type=str)
    define('email-template', default=DEFAULT_EMAIL_TEMPLATE,
           help='file with the template of the email sent when a page changes', type=str)
    define('email-error-template', default=DEFAULT_EMAIL_ERROR_TEMPLATE,
           help='file with the template of the email sent when a job fails', type=str)
    define('error-email-interval', default=ERROR_EMAIL_INTERVAL,
           help='seconds to wait before sending the error email again for the same error (0 to never resend)', type=int)
    define('user-agent', default='Diffido/%s (%s)' % (API_VERSION, PROJECT_URL),
           help='User-Agent header for outgoing HTTP/HTTPS requests', type=str)
    define('smtp-host', default='localhost', help='SMTP server address', type=str)
    define('smtp-port', default=0, help='SMTP server port', type=int)
    define('smtp-local-hostname', default=None, help='SMTP local hostname', type=str)
    define('smtp-use-ssl', default=False, help='Use SSL to connect to the SMTP server', type=bool)
    define('smtp-starttls', default=False, help='Use STARTTLS to connect to the SMTP server', type=bool)
    define('smtp-ssl-keyfile', default=None, help='SMTP SSL key file', type=str)
    define('smtp-ssl-certfile', default=None, help='SMTP SSL cert file', type=str)
    define('smtp-ssl-context', default=None, help='SMTP SSL context', type=str)
    define('smtp-username', default='', help='SMTP username', type=str)
    define('smtp-password', default='', help='SMTP password', type=str)
    define('debug', default=False, help='run in debug mode', type=bool)
    define('config', help='read configuration file',
            callback=lambda path: tornado.options.parse_config_file(path, final=False))
    if not options.config and os.path.isfile(DEFAULT_CONF):
        tornado.options.parse_config_file(DEFAULT_CONF, final=False)
    tornado.options.parse_command_line()
    if options.admin_email:
        EMAIL_FROM = options.admin_email
    EMAIL_TEMPLATE = options.email_template
    EMAIL_ERROR_TEMPLATE = options.email_error_template
    ERROR_EMAIL_INTERVAL = options.error_email_interval

    for key, value in options.as_dict().items():
        if key.startswith('smtp-'):
            SMTP_SETTINGS[key] = value

    if options.debug:
        logger.setLevel(logging.DEBUG)

    ssl_options = {}
    if os.path.isfile(options.ssl_key) and os.path.isfile(options.ssl_cert):
        ssl_options = dict(certfile=options.ssl_cert, keyfile=options.ssl_key)

    init_params = dict(listen_port=options.port, logger=logger, ssl_options=ssl_options,
                       scheduler=scheduler)
    git_init()

    _reset_schedules_path = r'schedules/reset'
    _schedule_run_path = r'schedules/(?P<id_>\d+)/run'
    _schedules_path = r'schedules/?(?P<id_>\d+)?'
    _history_path = r'schedules/?(?P<id_>\d+)/history'
    _diff_path = r'schedules/(?P<id_>\d+)/diff/(?P<commit_id>[0-9a-f]{7,40}|HEAD~?)/?(?P<old_commit_id>[0-9a-f]{7,40})?/?'
    _revision_path = r'schedules/(?P<id_>\d+)/revision/?(?P<commit_id>(?:[0-9a-f]{7,40}|HEAD~?))?/?'
    application = tornado.web.Application([
            (r'/api/%s' % _reset_schedules_path, ResetSchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _reset_schedules_path), ResetSchedulesHandler, init_params),
            (r'/api/%s' % _schedule_run_path, RunScheduleHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _schedule_run_path), RunScheduleHandler, init_params),
            (r'/api/%s' % _history_path, HistoryHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _history_path), HistoryHandler, init_params),
            (r'/api/%s' % _diff_path, DiffHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _diff_path), DiffHandler, init_params),
            (r'/api/%s' % _revision_path, RevisionHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _revision_path), RevisionHandler, init_params),
            (r'/api/%s' % _schedules_path, SchedulesHandler, init_params),
            (r'/api/v%s/%s' % (API_VERSION, _schedules_path), SchedulesHandler, init_params),
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
