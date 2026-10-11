# Dependencies and distribution

Diffido is distributed as a source checkout and a Docker image built from that
checkout. It has no Python wheel/sdist metadata, console entry point, or frontend
bundler. `dist/` contains maintained application templates, JavaScript and CSS;
its name does not imply that it is a generated release artifact. Run the server
from the checkout root because configuration and storage paths are relative to
the working directory.

## Audit and changes

Previously, the repository directly tracked Vue 2.5.13, Vue Material, Axios
0.21.0, Lodash, diff2html (including its bundled dependencies), and the Material
Icons font. Only diff2html and Material Icons were referenced by the current
plain JavaScript UI. The unused libraries and all tracked third-party assets
have been removed. Python libraries were already installed externally, but the
README and Dockerfile maintained separate dependency lists.

`package.json` now declares diff2html 3.4.56 and
material-design-icons-iconfont 6.7.0. `package-lock.json` records their complete
resolved npm dependency graph, download URLs and integrity hashes. The
application uses diff2html's `html()` API in place of the old `getPrettyHtml()`
API. Upstream references: [diff2html](https://github.com/rtfpessoa/diff2html) and
[Material Icons iconfont](https://github.com/jossef/material-design-icons-iconfont).

## Frontend installation

Run `npm ci --ignore-scripts` followed by `npm run build`. npm owns the external
packages under ignored `node_modules/`; the build copies browser assets and
license notices into ignored `dist/static/vendor/`. These directories are
regenerable installation artifacts and must not be committed. The generated
assets are served by Tornado locally; browser use requires no CDN access.
The runtime image necessarily includes installed third-party assets, just as it
includes installed Python libraries. Dependencies are no longer maintained as
copies in Diffido's source tree.

Node.js/npm are build tools only. Docker installs npm packages in a separate
Node 22 stage and copies the generated assets into the Python runtime image.
`.dockerignore` excludes local dependencies, generated assets, runtime data and
TLS private material from the build context. Mount `conf/` and `storage/` when
running the image, as `run-docker.sh` does. TLS files must be mounted at runtime.

To update a frontend dependency, change its explicit version, regenerate the
lockfile with `npm install --ignore-scripts`, then rebuild and verify both diff
layouts and the icons. Review upstream API and license changes before upgrading.
Do not edit files inside `node_modules/` or `dist/static/vendor/`.

## Backend installation and limits

`requirements.txt` is the common dependency manifest for source installations
and Docker: APScheduler, lxml, pytz, Requests, SQLAlchemy and Tornado. Git is an
external executable installed separately (via apk in Docker). APScheduler is
restricted to major version 3 because the scheduler and job-store APIs change
in version 4. Other ranges express compatibility boundaries, not a fully locked
Python environment.

The Python image and Node image use versioned tags, not immutable digests;
Python resolution, transitive packages and Alpine packages can change between
builds. Fully reproducible release images would additionally require locked
Python dependencies and image digests. No registry publication or deployment
is performed by this change. The repository has no automated test suite or CI
release workflow.
