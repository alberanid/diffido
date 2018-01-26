# Diffido development

## API

- /api/schedules GET - the list of all schedules
- /api/schedules POST - add a new schedule
- /api/schedules/reset POST - reset all schedules using the JSON configuration
- /api/schedules/:id GET - get a single schedule
- /api/schedules/:id PUT - update a schedule
- /api/schedules/:id DELETE - delete a schedule
- /api/schedules/:id/run POST - immediately run a schedule
- /api/schedules/:id/history GET - get the history of a schedule
- /api/schedules/:id/diff/:commit_id/:old_commit_id GET - get the diff from two commits of a schedule


## Data layout

Information are stored in:

- **conf/diffido.conf**: global options (Python syntax), can also be passed on the command line
- **conf/schedules.json**: the JSON file used to store the schedules settings
- **conf/jobs.db**: SQLite database used by APScheduler
- **storage/**: git repositories, one for each schedule


Coding style and conventions
----------------------------

It's enough to be consistent within the document you're editing.

I suggest four spaces instead of tabs for all the code: Python (**mandatory**), JavaScript, HTML and CSS.

Python code documented following the [Sphinx](http://sphinx-doc.org/) syntax.

