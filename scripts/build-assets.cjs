// Publish only runtime assets from npm packages; never edit dependency sources.
const { cpSync, mkdirSync, rmSync } = require('node:fs');
const { resolve } = require('node:path');
const root = resolve(__dirname, '..');
const output = resolve(root, 'dist/static/vendor');
rmSync(output, { recursive: true, force: true });
mkdirSync(output, { recursive: true });
for (const [source, target] of [
    ['diff2html/bundles/js/diff2html.min.js', 'diff2html/diff2html.min.js'],
    ['diff2html/bundles/css/diff2html.min.css', 'diff2html/diff2html.min.css'],
    ['diff2html/LICENSE.md', 'diff2html/LICENSE.md'],
    ['@profoundlogic/hogan/LICENSE', 'diff2html/hogan-LICENSE'],
    ['diff/LICENSE', 'diff2html/diff-LICENSE'],
    ['material-design-icons-iconfont/dist', 'material-icons'],
    ['material-design-icons-iconfont/LICENSE', 'material-icons/LICENSE'],
]) {
    cpSync(resolve(root, 'node_modules', source), resolve(output, target), { recursive: true });
}
