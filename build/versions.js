#!/usr/bin/env node
'use strict';
/*
 * The release table, from node-onlykey-lib, as JSON for the Python builder.
 *
 *     node build/versions.js            the table, as build/pins.py reads it
 *     node build/versions.js --check    one line; npm's postinstall runs this
 *
 * WHY THE TABLE IS NOT IN THIS REPO ANY MORE
 *
 * This repo used to carry its own ok-versions.json - a copy of the pins that
 * ok-rn, the emulator and the test kit each also carried - and build.py wrote a
 * `developer` map back into it. Copies drift: by 2026-09-30 this one still
 * listed v3.0.5 (dropped from the lib the day before) and had no v3.1.0 at all,
 * so the builder was building a release nobody else tested and skipping the one
 * everybody did. The lib owns the table now, pinned by commit in package.json
 * exactly as the emulator pins it; a new release is a lib bump, not an edit here.
 *
 * WHY A SEPARATE PROCESS RATHER THAN A PYTHON COPY OF THE LOGIC
 *
 * pinsFor() is where a half-pinned row is refused, and that rule must be the
 * lib's, not a re-implementation of it. This is the one crossing from JS to
 * Python; everything on the Python side goes through build/pins.py.
 *
 * `require('node-onlykey-lib/versions')` loads only src/versions and
 * src/device/version - no node-hid, no native build - so this runs anywhere
 * Node does, including Windows, where the builder itself cannot compile.
 */

const fs = require('fs');
const path = require('path');
const versions = require('node-onlykey-lib/versions');

const REPO = path.join(__dirname, '..');

/*
 * Which lib this table came from: its version, and the commit package-lock.json
 * resolved it to. The published page names it, so a reader can tell which table
 * a set of images was built against - "0.3.0" alone does not say, because the
 * lib is pinned by commit and its version field moves far less often.
 */
function libIdentity() {
  const dir = path.dirname(require.resolve('node-onlykey-lib/package.json'));
  const version = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).version;
  let commit = '';
  for (const lock of [path.join(REPO, 'package-lock.json'),
                      path.join(REPO, 'node_modules', '.package-lock.json')]) {
    try {
      const entry = JSON.parse(fs.readFileSync(lock, 'utf8'))
        .packages['node_modules/node-onlykey-lib'];
      commit = String((entry && entry.resolved) || '').split('#')[1] || '';
      if (commit) break;
    } catch (_) { /* try the next lockfile */ }
  }
  return { version, commit };
}

function table() {
  const releases = {};
  for (const v of versions.list()) {
    // pinsFor() THROWS on a half-pinned row, which is the point: that row would
    // build half a release, and it should stop the sweep before the compiler
    // starts rather than three hours in. null = a named-but-not-cut release,
    // emitted with blank pins so build.py's working-tree path takes it.
    const pins = versions.pinsFor(v);
    const compat = versions.compatibilityOf(v) || {};
    releases[v] = {
      libraries: pins ? pins.libraries : '',
      'OnlyKey-Firmware': pins ? pins['OnlyKey-Firmware'] : '',
      ...(pins && pins.file ? { file: pins.file } : {}),
      unreleased: Boolean(compat.unreleased),
      // The lib's MODEL of what a signed build of the release reports - not
      // anything probed from the .hex. The page says so.
      compatibility: compat.capabilities || null,
    };
  }
  return { lib: libIdentity(), releases };
}

const t = table();
if (process.argv.includes('--check')) {
  const names = Object.keys(t.releases);
  console.log('node-onlykey-lib %s @ %s: %d releases, %s .. %s',
    t.lib.version, (t.lib.commit || '?').slice(0, 7), names.length,
    names[0], names[names.length - 1]);
} else {
  process.stdout.write(JSON.stringify(t, null, 1) + '\n');
}
