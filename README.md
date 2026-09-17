# Diffido

Spot the difference on the web.

Tired of clicking F5 waiting for a change on a web page? Define a list of pages to watch, and receive an email when something has changed.


## Install, run, develop and debug

## Docker

Just run:

    ./run-docker.sh


## Old-fashioned installation

To install it:
``` bash
wget https://bootstrap.pypa.io/get-pip.py
sudo python3 get-pip.py
# if you want to install these modules for an unprivileged user, add --user and remove "sudo";
# if you want to upgrade the versions already present in the system, also add --upgrade
sudo pip3 install lxml
sudo pip3 install pytz
sudo pip3 install apscheduler
sudo pip3 install requests
sudo pip3 install sqlalchemy
sudo pip3 install tornado
git clone https://github.com/alberanid/diffido
cd diffido
./diffido.py --debug
```

Now you can **point your browser to [http://localhost:3210/](http://localhost:3210/)**

You can also **run the server in https**, putting in the *ssl* directory two files named *diffido_key.pem* and *diffido_cert.pem*


# Settings

You can edit the *conf/diffido.conf* file (Python syntax) to change the global settings; you almost surely have to configure the SMTP settings, at least.

The *user_agent* setting is the `User-Agent` header sent with the requests used to check pages; its format should follow the [Wikimedia Foundation User-Agent Policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy) (`<client name>/<version> (<contact information>) <library>/<version>`).

Each schedule has its own web page GUI; the settings should be pretty self-explanatory, except:

- **XPath selector**: define which portion of a web page to consider
- **minimum change**: float between 0.0 and 1.0, which represent the minimum amount of the page (in percentage of number of lines) that has to be changed to send a notification; if left empty, any change will be notified
- **crontab**: a complete crontab definition, to specify the period of the check

Each schedule can also customize the HTTP request used to fetch the page:

- **Method**: the HTTP method (`GET`, `POST`, `PUT`, `DELETE` or `OPTIONS`; default `GET`). Non-GET methods additionally show the *Request body* and *Content type* fields.
- **Authentication**: `Basic` and `Digest` use the *Username* and *Password* fields; `Bearer token` uses the *Bearer token* field (for example a long-lived JWT, transported as a bearer token).
- **Custom headers**: one `Name: value` pair per line (for example `X-Api-Key: secret`); they override any header set by the other options, including the default `User-Agent` and `Authorization`.
- **Cookies**: static cookies, as `name=value` pairs separated by semicolons (for example `session=xyz`); useful to fetch pages that require a logged-in session (copy the cookies from your browser). Automatic login with credentials is not supported.
- **Request body** and **Content type**: body sent with non-GET requests; when a body is set but no content type is, `application/x-www-form-urlencoded` is used.

Credentials are stored in plaintext in *conf/schedules.json* (which is gitignored): treat that file accordingly. Note that wrong credentials are not reported as job failures: the server's error or login page is fetched and diffed like any other page, which will surface as (unwanted) changes.


# Notifications

When a watched page changes (and the change is above the configured *minimum change*), Diffido sends an email to the address configured for the schedule. The message body reports the schedule ID and title, the monitored URL, the number of insertions and deletions, the Git revisions involved and the date of the change; the unified diff is sent as a `.diff` attachment instead of being embedded in the body. Errors encountered while running a job are reported to the *admin_email* address (or to the configured SMTP username).

To avoid flooding the administrator, an error email is sent only the first time a given error occurs: identical errors from the same schedule are skipped, until either the error changes, the job succeeds again, or the same error has persisted for more than *error_email_interval* seconds (default: 86400, i.e. one day; set it to 0 to never resend the same error). The last notified errors are remembered in *conf/error_state.json*.


## Email templates

The emails are built from two templates, both editable:

- *conf/email_template.txt*: notification sent when a page changes (change the *email_template* setting to use a different file)
- *conf/email_error_template.txt*: notification sent when a job fails (change the *email_error_template* setting to use a different file)

Both files are read every time an email is sent, so changes take effect without restarting the server. If a file is missing or unreadable, the built-in template is used.

The first line of a template is the subject of the email, when it starts with `Subject:`; all the remaining lines are the body. Placeholders use `$name` syntax and are expanded with the values of the schedule and of the change that triggered the email; unknown placeholders are left untouched.

Placeholders available in *conf/email_template.txt*:

- `$id`: ID of the schedule
- `$title`: title of the schedule (or *diffido*, when not set)
- `$title_suffix`: ` - <title>`, when the schedule has a title
- `$url`: monitored URL
- `$insertions`, `$deletions`: number of inserted and deleted lines
- `$changes`: number of changed lines
- `$previous_lines`: number of lines of the previous revision
- `$previous_revision`, `$current_revision`: Git revisions involved
- `$date`: date of the change
- `$xpath`: XPath selector of the schedule, when set
- `$minimum_change`: minimum change of the schedule, when set
- `$previous_revision_line`, `$current_revision_line`, `$date_line`, `$xpath_line`, `$minimum_change_line`: the corresponding line (including the newline), when the value is set, or an empty string

Placeholders available in *conf/email_error_template.txt*:

- `$id`: ID of the schedule
- `$url`: monitored URL, when set
- `$url_suffix`: ` (<url>)`, when the schedule has a URL
- `$error`: the error that prevented the job from running

For example, a minimal change notification template is:

```
Subject: $title changed ($changes lines)
$url
$current_revision_line
The unified diff is attached to this email.
```


# Development

See the *docs/DEVELOPMENT.md* file for more information about how to contribute.


## Technological stack

- [VueJS 2](https://vuejs.org/) for the webApp
- [Vue Material](https://vuematerial.github.io/) for the UI components
- [Tornado web](http://www.tornadoweb.org/) as web server
- [APScheduler](https://github.com/agronholm/apscheduler) to run the scheduled jobs
- [Python 3](https://www.python.org/)
- [Git](https://git-scm.com/) to store the data


# License and copyright

Copyright 2018-2026 Davide Alberani <da@mimante.net>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

