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

Each schedule has its own web page GUI; the settings should be pretty self-explanatory, except:

- **XPath selector**: define which portion of a web page to consider
- **minimum change**: float between 0.0 and 1.0, which represent the minimum amount of the page (in percentage of number of lines) that has to be changed to send a notification; if left empty, any change will be notified
- **crontab**: a complete crontab definition, to specify the period of the check


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

Copyright 2018 Davide Alberani <da@erlug.linux.it>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

