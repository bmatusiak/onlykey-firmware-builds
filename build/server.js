#!/usr/bin/env node
'use strict';
/*
 * Live status for the firmware matrix build.
 *
 *     node build/server.js [port]        default 8090
 *
 * SHOWS THE WHOLE MATRIX, NOT THE CURRENT INVOCATION.
 *
 * A sweep is many hours and gets interrupted - a reboot, a killed session, a
 * change of mind halfway. The useful question is never "what is this one
 * command doing", it is "what of the matrix exists, what is missing, and what
 * is happening right now". So the page is assembled from three files with
 * three different lifetimes:
 *
 *     developer_firmware/matrix.json     every combination and whether it CAN exist at its
 *                         pin, from applying the build gates. Rewritten each
 *                         run; cheap, seconds.
 *     developer_firmware/index.json      every variant ever built, cumulative, keyed by name.
 *                         Survives reboots and unrelated runs.
 *     work/status.json    what the running build is doing this second.
 *                         Absent when nothing is running, which is fine.
 *
 * A variant with no entry anywhere is simply not built yet. The page never
 * pretends a build is missing because a command has ended.
 *
 * This server reads and never writes or controls anything, so it cannot harm a
 * build and killing it costs nothing.
 *
 * ZERO DEPENDENCIES, DELIBERATELY. `ws` would put an npm install between you
 * and a status page, on a machine whose node lives under nvm and is not on the
 * non-interactive PATH. The server side of the WebSocket protocol for text
 * frames is about forty lines; it is written out below rather than depended on.
 */

const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..');
const WORK = path.join(REPO, 'work');
const OUT = path.join(REPO, 'developer_firmware');
const STATUS = path.join(WORK, 'status.json');
const LOGS = path.join(OUT, 'logs');
const FAILURES = path.join(OUT, 'failures.json');
const MATRIX = path.join(OUT, 'matrix.json');
const INDEX = path.join(OUT, 'index.json');
const PORT = Number(process.argv[2] || 8090);
const TAIL_LINES = 22;

/* ---------------------------------------------------------------- reading */

function readJSON(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return fallback; }
}

// Logs are per variant and are never overwritten, so any build ever run can be
// read back - which is the whole point of keeping them. A name is only ever
// used to build a filename after being checked against the matrix, so a crafted
// request cannot walk out of the logs directory.
function logPath(name) {
  if (!/^[A-Za-z0-9._-]+$/.test(name || '')) return null;
  return path.join(LOGS, name + '.log');
}

function readTail(file, maxLines) {
  try {
    const size = fs.statSync(file).size;
    const from = Math.max(0, size - 64 * 1024);
    const fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(size - from);
    fs.readSync(fd, buf, 0, buf.length, from);
    fs.closeSync(fd);
    const lines = buf.toString('utf8').split('\n').filter((l) => l.trim());
    return { lines: lines.slice(-(maxLines || TAIL_LINES)), bytes: size };
  } catch (e) {
    return { lines: [], bytes: 0 };
  }
}

function snapshot() {
  const status = readJSON(STATUS, null);
  const cur = status && status.current ? status.current.name : null;
  const lp = cur ? logPath(cur) : null;
  return JSON.stringify({
    matrix: readJSON(MATRIX, []),
    index: readJSON(INDEX, {}),
    failures: readJSON(FAILURES, {}),
    status: status,
    tail: lp ? readTail(lp) : { lines: [], bytes: 0 },
    logs: fs.existsSync(LOGS) ? fs.readdirSync(LOGS).map(f => f.replace(/[.]log$/, '')) : [],
    now: Date.now() / 1000,
  });
}

/* ------------------------------------------------------------- websocket */

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
const clients = new Set();

function frame(text) {
  // Server-to-client text frame: FIN set, opcode 1, never masked.
  const payload = Buffer.from(text, 'utf8');
  const n = payload.length;
  let head;
  if (n < 126) {
    head = Buffer.from([0x81, n]);
  } else if (n < 65536) {
    head = Buffer.alloc(4);
    head[0] = 0x81; head[1] = 126; head.writeUInt16BE(n, 2);
  } else {
    head = Buffer.alloc(10);
    head[0] = 0x81; head[1] = 127;
    head.writeUInt32BE(0, 2); head.writeUInt32BE(n, 6);
  }
  return Buffer.concat([head, payload]);
}

function broadcast() {
  if (!clients.size) return;
  const buf = frame(snapshot());
  for (const s of clients) {
    if (s.writable) s.write(buf); else clients.delete(s);
  }
}

/* ------------------------------------------------------------------ page */

const PAGE = `<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>firmware matrix</title>
<style>
  :root{--bg:#0f1115;--fg:#d8dee9;--dim:#78818f;--line:#232833;--card:#161a21;
        --ok:#7fb069;--run:#e0b755;--fail:#cf6679;--none:#2a2f3a}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);padding:16px;padding-block:20px;
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  h1{font-size:15px;margin:0 0 2px;font-weight:600}
  .sub{color:var(--dim);font-size:12px}
  .bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin:12px 0 6px}
  .bar>i{display:block;height:100%;background:var(--ok);transition:width .4s}
  .stats{display:flex;gap:16px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-bottom:16px}
  .stats b{color:var(--fg)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:6px;
        padding:11px 13px;margin-bottom:16px}
  .now{font-size:14px;font-weight:600}
  .wrap{overflow-x:auto;margin-bottom:16px}
  table{border-collapse:collapse;font-size:11.5px;min-width:100%}
  th,td{padding:4px 6px;text-align:center;white-space:nowrap}
  th{color:var(--dim);font-weight:500;border-bottom:1px solid var(--line);font-size:10.5px}
  th.rel,td.rel{text-align:left;color:var(--fg);position:sticky;left:0;background:var(--bg);
                padding-right:12px}
  td.c{border-radius:3px;min-width:58px;color:#0f1115;font-weight:600}
  .s-done{background:var(--ok)}
  .s-building{background:var(--run);animation:pulse 1.4s ease-in-out infinite}
  .s-failed{background:var(--fail)}
  .s-pending{background:var(--none);color:var(--dim);font-weight:400}
  .s-na{color:#3c424e;font-weight:400}
  @keyframes pulse{50%{opacity:.55}}
  .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--dim);margin-bottom:18px}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;
            vertical-align:-1px}
  pre{background:#0b0d11;border:1px solid var(--line);border-radius:6px;padding:10px;margin:0;
      overflow:auto;font-size:11px;color:var(--dim);max-height:300px}
  .off{color:var(--fail)}
  td.has-log{cursor:pointer}
  td.has-log:hover{outline:2px solid var(--fg);outline-offset:-2px}
  #viewer{position:fixed;inset:5% 4%;background:var(--card);border:1px solid var(--line);
          border-radius:8px;padding:12px;display:flex;flex-direction:column;z-index:10;
          box-shadow:0 18px 60px rgba(0,0,0,.6)}
  #viewer[hidden]{display:none}
  .vbar{display:flex;gap:12px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
  .vbar b{font-size:13px}
  .vbar button,.vbar a{background:var(--line);color:var(--fg);border:0;border-radius:4px;
       padding:3px 10px;font:inherit;font-size:11px;cursor:pointer;text-decoration:none}
  #vlog{flex:1;max-height:none;white-space:pre-wrap;word-break:break-word}
</style>
<h1>onlykey firmware matrix</h1>
<div class="sub" id="conn">connecting…</div>
<div class="bar"><i id="fill" style="width:0"></i></div>
<div class="stats">
  <div><b id="built">0</b>/<b id="buildable">0</b> built</div>
  <div>failed <b id="failed">0</b></div>
  <div>not available <b id="na">0</b></div>
  <div>remaining <b id="left">0</b></div>
  <div>elapsed <b id="elapsed">–</b></div>
  <div>eta <b id="eta">–</b></div>
  <div>machine time <b id="cpu">–</b></div>
</div>
<div class="card" id="current"><div class="now">idle</div></div>
<div class="wrap"><table id="grid"></table></div>
<div class="legend">
  <span><i style="background:var(--ok)"></i>built</span>
  <span><i style="background:var(--run)"></i>building</span>
  <span><i style="background:var(--fail)"></i>failed</span>
  <span><i style="background:var(--none)"></i>not built yet</span>
  <span><i style="background:#3c424e"></i>– cannot exist at this pin</span>
</div>
<div class="sub" style="margin-bottom:6px" id="logtitle">live output — current build</div>
<pre id="log">no build output yet</pre>
<div id="viewer" hidden>
  <div class="vbar">
    <b id="vname"></b>
    <span id="vwhy" class="off"></span>
    <button onclick="closeLog()">close</button>
    <a id="vraw" href="#" target="_blank">raw</a>
  </div>
  <pre id="vlog">loading…</pre>
</div>
<script>
const $ = (id) => document.getElementById(id);
const hms = (s) => {
  if (s == null || !isFinite(s)) return '–';
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  return h ? h+'h'+m+'m' : (m ? m+'m'+(s%60)+'s' : s+'s');
};
// Two models by two build types. The IN TRVL edition was briefly a third axis
// and was removed - it cannot link on any pin. See the FINDING in the repo root.
const COLS = [
  ['classic','test','classic · test'], ['classic','prod','classic · prod'],
  ['duo','test','DUO · test'], ['duo','prod','DUO · prod'],
];

function render(d) {
  LAST = d;
  const matrix = d.matrix || [], index = d.index || {}, st = d.status;
  const byName = {};
  for (const m of matrix) byName[m.name] = m;

  const cur = st && st.current ? st.current.name : null;
  // Durable failures first, then anything this run has hit. A variant that
  // failed last night is still failed this morning, and the reason is still
  // worth showing - that was the whole point of keeping the logs.
  const failedNow = {};
  for (const [k, v] of Object.entries(d.failures || {})) failedNow[k] = v.why;
  if (st) for (const f of (st.failures||[])) failedNow[f[0]] = f[1];

  const state = (m) => {
    if (!m.available) return 'na';
    if (m.name === cur) return 'building';
    if (index[m.name]) return 'done';
    if (failedNow[m.name]) return 'failed';
    return 'pending';
  };

  const buildable = matrix.filter(m => m.available);
  const built = buildable.filter(m => index[m.name]).length;
  const nfail = Object.keys(failedNow).length;
  const na = matrix.length - buildable.length;
  const left = buildable.length - built;

  $('built').textContent = built;
  $('buildable').textContent = buildable.length;
  $('failed').textContent = nfail;
  $('na').textContent = na;
  $('left').textContent = left;
  $('fill').style.width = buildable.length ? (built/buildable.length*100)+'%' : '0';

  // Elapsed for THIS sweep, ticking on its own between builds because the
  // heartbeat re-renders every few seconds. Without it a long build looks
  // indistinguishable from a stalled one.
  const started = st && st.started;
  $('elapsed').textContent = started
    ? hms((st.finished || d.now) - started) + (st.finished ? '' : ' …')
    : '–';

  // Total compile time ever recorded, across every sweep. Not the same as
  // elapsed: skips cost nothing, and a matrix built over three sessions has a
  // machine time much larger than any one sweep's wall clock.
  const allSecs = Object.values(index).map(r=>r.seconds).filter(Boolean);
  $('cpu').textContent = allSecs.length
    ? hms(allSecs.reduce((a,b)=>a+b,0)) : '–';

  // ETA from measured pace across EVERY build ever recorded, not just this
  // invocation - the matrix gets built over several sessions.
  const times = Object.values(index).map(r=>r.seconds).filter(Boolean);
  const avg = times.length ? times.reduce((a,b)=>a+b,0)/times.length : null;
  $('eta').textContent = left === 0 ? 'complete' : (avg ? hms(avg*left) : '–');

  const c = st && st.current;
  $('current').innerHTML = c
    ? '<div class="now">' + c.name + '</div><div class="sub" style="margin-top:3px">'
      + c.release + ' · ' + c.model + ' · ' + c.build
      + ' · running ' + hms(d.now - c.started) + '</div>'
    : '<div class="now sub">nothing building</div>';

  // Rows in the order the pins file gives, which is newest release first.
  const rels = [];
  for (const m of matrix) if (!rels.includes(m.release)) rels.push(m.release);

  let html = '<tr><th class="rel">release</th>'
    + COLS.map(c=>'<th>'+c[2]+'</th>').join('') + '</tr>';
  for (const rel of rels) {
    html += '<tr><td class="rel">' + rel + '</td>';
    for (const [model,build] of COLS) {
      const name = rel+'-'+model+'-'+build;
      const m = byName[name];
      if (!m) { html += '<td class="c s-pending">?</td>'; continue; }
      const s = state(m);
      const rec = index[name];
      const label = s === 'na' ? '–'
        : s === 'done' ? hms(rec.seconds)
        : s === 'building' ? (st && st.current ? hms(d.now - st.current.started) : '···')
        : s === 'failed' ? 'fail' : '·';
      const tip = s === 'na' ? (m.why||'not available at this pin')
        : s === 'done' ? (rec.program_bytes + ' bytes · ' + (rec.sha256||'').slice(0,12))
        : s === 'failed' ? failedNow[name] : name;
      const hasLog = (d.logs||[]).includes(name);
      html += '<td class="c s-'+s+(hasLog?' has-log':'')+'"'
        + (hasLog ? ' data-log="'+name+'"' : '')
        + ' title="'+String(tip).replace(/"/g,'&quot;')+(hasLog?' — click for the build log':'')+'">'
        + label+'</td>';
    }
    html += '</tr>';
  }
  $('grid').innerHTML = html;

  $('logtitle').textContent = c
    ? 'live output — ' + c.name
    : 'live output — nothing building (click any built or failed cell for its log)';
  const lines = (d.tail && d.tail.lines) || [];
  const el = $('log');
  const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  el.textContent = lines.length ? lines.join('\\n') : 'no build output yet';
  if (stick) el.scrollTop = el.scrollHeight;
}

// The failure reason for whichever variant the viewer is showing, so it can be
// put above the log rather than leaving you to find it in 30000 lines.
let LAST = {};
function showLog(name) {
  document.getElementById('vname').textContent = name;
  const why = (LAST.failures||{})[name];
  document.getElementById('vwhy').textContent = why ? why.why : '';
  document.getElementById('vraw').href = '/log?name=' + encodeURIComponent(name);
  document.getElementById('viewer').hidden = false;
  const el = document.getElementById('vlog');
  el.textContent = 'loading…';
  fetch('/log?name=' + encodeURIComponent(name))
    .then(r => r.text())
    .then(t => {
      el.textContent = t || '(the log is empty)';
      // A failed build is read from the END - the error is the last thing in
      // it, and these logs run to tens of thousands of lines.
      el.scrollTop = el.scrollHeight;
    })
    .catch(e => { el.textContent = 'could not read the log: ' + e; });
}
function closeLog() { document.getElementById('viewer').hidden = true; }
addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLog(); });

document.getElementById('grid').addEventListener('click', (e) => {
  const td = e.target.closest('td[data-log]');
  if (td) showLog(td.dataset.log);
});

let ws;
function connect() {
  ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host);
  ws.onopen = () => { $('conn').textContent='live'; $('conn').className='sub'; };
  ws.onmessage = (e) => render(JSON.parse(e.data));
  ws.onclose = () => {
    $('conn').textContent='disconnected — retrying';
    $('conn').className='sub off';
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
}
connect();
</script>`;

/* ---------------------------------------------------------------- server */

const server = http.createServer((req, res) => {
  // One variant's full log, for the page's log viewer. Any build ever run,
  // not just the current one - that is what keeping them per variant buys.
  if (req.url.startsWith('/log?')) {
    const name = new URL(req.url, 'http://x').searchParams.get('name');
    const lp = logPath(name);
    if (!lp || !fs.existsSync(lp)) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('no log for that variant - it may not have been built yet');
      return;
    }
    res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' });
    res.end(fs.readFileSync(lp));
    return;
  }
  if (req.url === '/status.json') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(snapshot());
    return;
  }
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(PAGE);
});

server.on('upgrade', (req, socket) => {
  const key = req.headers['sec-websocket-key'];
  if (!key) return socket.destroy();
  const accept = crypto.createHash('sha1').update(key + GUID).digest('base64');
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
    'Upgrade: websocket\r\nConnection: Upgrade\r\n' +
    'Sec-WebSocket-Accept: ' + accept + '\r\n\r\n');
  socket.setNoDelay(true);
  clients.add(socket);
  socket.write(frame(snapshot()));
  // The client never needs to say anything, so incoming frames are read only
  // far enough to notice a close (opcode 8).
  socket.on('data', (b) => { if ((b[0] & 0x0f) === 0x8) socket.destroy(); });
  socket.on('close', () => clients.delete(socket));
  socket.on('error', () => clients.delete(socket));
});

let dirty = true;
for (const f of [STATUS, MATRIX, INDEX, FAILURES]) {
  try { fs.watch(f, () => { dirty = true; }); } catch (e) { /* not there yet */ }
}
setInterval(() => { if (dirty) { dirty = false; broadcast(); } }, 500);
// Slow heartbeat so a running timer ticks up on its own, and so a dead server
// looks dead rather than looking like a stalled build.
setInterval(() => broadcast(), 3000);
// fs.watch never fires for a file that did not exist at startup, which is the
// normal case before the first run. Poll cheaply for their arrival.
setInterval(() => {
  for (const f of [STATUS, MATRIX, INDEX, FAILURES]) {
    try { fs.statSync(f); dirty = true; } catch (e) { /* still absent */ }
  }
}, 2000);

server.listen(PORT, '0.0.0.0', () => {
  console.log('firmware matrix status on http://0.0.0.0:' + PORT);
});
