#!/usr/bin/env node
'use strict';
/*
 * Every pinned commit in the release table resolves in the sibling checkouts.
 *
 *     node build/check-pins.js          exit 0, or 1 naming each pin that is missing
 *
 * WHY THIS RUNS BEFORE A BUILD, NOT AFTER
 *
 * build.py re-surveys EVERY pinned release on every run - `git show <pin>` in
 * the libraries checkout (write_survey) - even a run that only builds "latest".
 * A pin the checkout does not have is not an error there: the exception is
 * caught and the release is written into matrix.json as "unavailable". On the
 * Pi that cannot happen, its checkouts have the whole history. In CI it can: a
 * shallow clone, or a fork that never had trustcrypto's release commits, and
 * the GitHub Actions "latest" job would commit a matrix.json - and so a docs
 * page - that says every release is unbuildable. So the job asks first, and
 * stops before building anything when the answer is no.
 *
 * The table is the lib's (build/versions.js); a blank pin is a working-tree row
 * ("latest") and has nothing to resolve.
 */
const {execFileSync} = require('child_process');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const ROOT = path.dirname(REPO);
const SIBLING = {libraries: 'libraries', 'OnlyKey-Firmware': 'OnlyKey-Firmware'};

const table = JSON.parse(execFileSync(process.execPath, [path.join(__dirname, 'versions.js')],
  {encoding: 'utf8', maxBuffer: 16 * 1024 * 1024}));

const missing = [];
let checked = 0;
for (const [release, row] of Object.entries(table.releases)) {
  for (const [key, dir] of Object.entries(SIBLING)) {
    const pin = row[key];
    if (!pin) continue;
    checked++;
    try {
      execFileSync('git', ['-C', path.join(ROOT, dir), 'cat-file', '-e', `${pin}^{commit}`],
        {stdio: 'ignore'});
    } catch (err) {
      missing.push(`${release}: ${dir} has no commit ${pin}`);
    }
  }
}

if (missing.length) {
  console.error(`check-pins: ${missing.length} of ${checked} pinned commits are missing:`);
  for (const m of missing) console.error(`  ${m}`);
  console.error('Fetch the full history (and trustcrypto\'s release branches) into the checkouts.');
  process.exit(1);
}
console.log(`check-pins: all ${checked} pinned commits resolve in ${Object.values(SIBLING).join(' and ')}`);
