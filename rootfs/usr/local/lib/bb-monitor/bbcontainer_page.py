# The Container tab: the container's own settings and its update notices.
#
# Kept out of bb-monitor-web, which is already one very large file, and written as a raw
# string so the JavaScript below needs no escaping. bb-monitor-web calls html() with its
# own theme and page CSS, so this tab follows whichever of the thirteen themes was chosen
# in the Monitor, and anything it adds to those later.
#
# The page holds no setting names or limits of its own. It draws what /manage/container
# returns (bbcontainer.state) and posts back only what the person changed, so a setting
# added to bbcontainer.REGISTRY appears here, in the API and in the Unraid template with
# no further edit.

PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backblaze 64 Container</title>
<style>
@@THEME_CSS@@
@@PAGE_CSS@@
  *{box-sizing:border-box;}
  body{margin:0 auto;max-width:1132px;padding:16px;background:var(--bg);color:var(--text);
    font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;line-height:1.5;}
  h1{font-size:18px;margin:0 0 4px;color:var(--title);}
  h2{font-size:14px;margin:0 0 4px;color:var(--title);font-weight:600;}
  p{margin:0 0 10px;max-width:100ch;}
  .dim{color:var(--dim);}
  .panel{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:16px;}
  .banner{border:1px solid var(--warn);border-radius:6px;padding:10px 14px;margin-bottom:16px;background:var(--panel);}
  .banner.bad{border-color:var(--bad);}
  .banner.good{border-color:var(--good);}
  .banner ul{margin:6px 0 0 18px;padding:0;}
  .row{display:grid;grid-template-columns:1fr;gap:4px 16px;padding:12px 0;border-top:1px solid var(--track-border);}
  .row:first-of-type{border-top:0;}
  @media (min-width:760px){ .row{grid-template-columns:minmax(0,1fr) 260px;align-items:start;} }
  .row .name{font-weight:600;}
  .row .name code{font-weight:400;font-size:12px;color:var(--dim);margin-left:6px;}
  .row .sum{font-size:12px;color:var(--dim);margin:2px 0 0;max-width:80ch;}
  .row .warn{font-size:12px;color:var(--warn);margin:2px 0 0;max-width:80ch;}
  .row .meta{font-size:12px;color:var(--dim);margin-top:4px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
  .row .ctl{display:flex;flex-direction:column;gap:6px;align-items:flex-start;}
  .row input[type=text],.row input[type=number],.row input[type=password],.row select{width:100%;padding:6px 8px;
    border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--text);font:inherit;}
  .chip{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
    padding:1px 7px;border-radius:9px;border:1px solid var(--dim);color:var(--dim);}
  .chip.setting{border-color:var(--title);color:var(--title);}
  .chip.pending{border-color:var(--warn);color:var(--warn);}
  .chip.good{border-color:var(--good);color:var(--good);}
  .chip.bad{border-color:var(--bad);color:var(--bad);}
  button{font:inherit;padding:6px 12px;border-radius:4px;border:1px solid var(--border);background:var(--bg);
    color:var(--text);cursor:pointer;}
  button.primary{background:var(--title);color:var(--bg);border-color:var(--title);font-weight:600;}
  button.link{border:0;background:none;padding:0;color:var(--title);text-decoration:underline;font-size:12px;}
  button:disabled{opacity:.5;cursor:default;}
  .switch{display:inline-flex;gap:8px;align-items:center;font-size:13px;cursor:pointer;margin:0;}
  .switch input{appearance:none;-webkit-appearance:none;width:38px;height:20px;margin:0;border-radius:10px;
    background:var(--dim);position:relative;cursor:pointer;border:0;flex:0 0 auto;}
  .switch input:checked{background:var(--good);}
  .switch input::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;
    background:var(--bg);transition:left .15s;}
  .switch input:checked::after{left:20px;}
  .bar{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--border);padding:10px 0;
    display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
  .upd{display:grid;grid-template-columns:150px 1fr;gap:4px 14px;padding:8px 0;border-top:1px solid var(--track-border);}
  .upd:first-of-type{border-top:0;}
  #msg.bad{color:var(--bad);} #msg.good{color:var(--good);}
</style></head><body>
<h1>Container</h1>
<p class="dim">Settings the container reads when it starts, and whether anything newer than what it runs exists. A change made
here is saved on your appdata share and applied at the next start of the container. A value set here wins over the same variable
in the Docker template; <em>Follow the container</em> puts it back.</p>

<div id="banners"></div>

<div class="panel" id="updates">
  <h2>Updates</h2>
  <p class="dim" id="upd-intro">Checking...</p>
  <div id="upd-rows"></div>
  <div style="margin-top:10px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
    <button id="upd-check">Check now</button><span class="dim" id="upd-when"></span>
  </div>
  <p class="dim" style="margin-top:10px;">To switch image tag (<code>latest</code> WineHQ stable, <code>latest-patched</code> the fork's
  Wine, <code>beta</code>, <code>ubuntu26</code>), edit this container in Unraid's Docker tab and change the Repository to
  <code>allornothing/backblaze-personal:&lt;tag&gt;</code>. On a patched or beta tag, unpin the client.</p>
  <p class="dim" style="margin-top:10px;">To update the image itself, open Unraid's <strong>Docker</strong> tab, choose
  <em>Check for updates</em>, then <em>apply update</em> on this container. Notices go to the endpoints on the Settings tab
  (events <em>Container image update</em>, <em>Newer Wine</em> and <em>Newer Backblaze client</em>).</p>
</div>

<div id="groups"></div>

<div class="bar">
  <button class="primary" id="save" disabled>Save changes</button>
  <button id="restart" hidden>Restart the container</button>
  <span id="msg" class="dim"></span>
</div>

<script>
(function(){
'use strict';
var $ = function(id){ return document.getElementById(id); };
var STATE = null, DIRTY = {}, LAST_UPD = null;

function el(tag, attrs, kids){
  var e = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function(k){
    if(k === 'text') e.textContent = attrs[k];
    else if(k === 'cls') e.className = attrs[k];
    else if(k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), attrs[k]);
    else if(attrs[k] !== false && attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k] === true ? '' : attrs[k]);
  });
  (kids || []).forEach(function(c){ if(c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
  return e;
}
function post(path, body){
  return fetch(path, {method:'POST', cache:'no-store',
    headers:{'Content-Type':'application/json', 'X-Requested-With':'bb64'}, body: JSON.stringify(body || {})})
    .then(function(r){ return r.json().then(function(b){ return {ok: r.ok, body: b}; }); });
}
function one(k, v){ var o = {}; o[k] = v; return o; }
function get(path){ return fetch(path, {cache:'no-store'}).then(function(r){ return r.json(); }); }
function say(text, cls){ var m = $('msg'); m.textContent = text || ''; m.className = cls || 'dim'; }

// ---- settings ----
function isOn(v){ return ['1','true','yes','on','y'].indexOf(String(v).toLowerCase()) >= 0; }

function control(s){
  var cur = s.kind === 'secret' ? '' : s.value;
  var input;
  if(s.kind === 'bool01' || s.kind === 'booltf'){
    var on = isOn(s.value);
    input = el('input', {type:'checkbox', 'data-var': s.var});
    input.checked = on;
    input.addEventListener('change', function(){
      var v = s.kind === 'bool01' ? (input.checked ? '1' : '0') : (input.checked ? 'true' : 'false');
      if(v === String(s.value)) delete DIRTY[s.var]; else DIRTY[s.var] = v;
      refreshSave();
    });
    return el('label', {cls:'switch'}, [input, el('span', {text: 'On'})]);
  }
  if(s.kind === 'enum'){
    input = el('select', {'data-var': s.var}, s.choices.map(function(c){
      var o = el('option', {value:c, text:c}); if(c === s.value) o.selected = true; return o; }));
  } else if(s.kind === 'int'){
    input = el('input', {type:'number', 'data-var': s.var, min: s.lo, max: s.hi, value: cur});
  } else if(s.kind === 'secret'){
    input = el('input', {type:'password', 'data-var': s.var, autocomplete:'new-password',
      placeholder: s.set ? 'set - type to replace' : 'not set'});
  } else {
    input = el('input', {type:'text', 'data-var': s.var, value: cur, spellcheck:'false'});
    if(s.var === 'TZ'){
      input.setAttribute('list', 'tzlist');
    }
  }
  input.addEventListener('input', function(){
    if(s.kind === 'secret'){ if(input.value === '') delete DIRTY[s.var]; else DIRTY[s.var] = input.value; }
    else if(String(input.value) === String(s.value)) delete DIRTY[s.var];
    else DIRTY[s.var] = input.value;
    refreshSave();
  });
  return input;
}

function row(s){
  var meta = [];
  meta.push(el('span', {cls:'chip ' + (s.source === 'setting' ? 'setting' : ''), text: s.source === 'setting' ? 'set here' : 'from the container'}));
  if(s.pending) meta.push(el('span', {cls:'chip pending', text:'applies at next start'}));
  if(s.kind !== 'secret' && s.source === 'setting') meta.push(el('span', {text:'the container says: ' + (s.container_value === '' ? '(empty)' : s.container_value)}));
  if(s.kind !== 'secret' && s.running !== undefined && String(s.running) !== String(s.value)){
    meta.push(el('span', {text:'running now: ' + (s.running === '' ? '(empty)' : s.running)}));
  }
  var left = el('div', {}, [
    el('div', {cls:'name'}, [s.title, el('code', {text: s.var})]),
    el('p', {cls:'sum', text: s.summary}),
    s.warning ? el('p', {cls:'warn', text: s.warning}) : null,
    el('div', {cls:'meta'}, meta)
  ]);
  var ctl = el('div', {cls:'ctl'}, [control(s)]);
  if(s.stored){
    ctl.appendChild(el('button', {cls:'link', text:'Follow the container',
      onclick: function(){ save(one(s.var, null)); }}));
  }
  if(s.kind === 'secret' && s.stored){
    ctl.appendChild(el('button', {cls:'link', text:'Clear it', onclick: function(){
      if(confirm('Remove the stored ' + s.title.toLowerCase() + '?')) save(one(s.var, null)); }}));
  }
  return el('div', {cls:'row'}, [left, ctl]);
}

function render(state){
  STATE = state; DIRTY = {};
  var box = $('groups'); box.textContent = '';
  var tz = $('tzlist');
  if(!tz){
    tz = el('datalist', {id:'tzlist'});
    try { (Intl.supportedValuesOf('timeZone') || []).forEach(function(z){ tz.appendChild(el('option', {value:z})); }); } catch(e){}
    document.body.appendChild(tz);
  }
  state.groups.forEach(function(g){
    var rows = state.settings.filter(function(s){ return s.group === g.id; });
    if(!rows.length) return;
    box.appendChild(el('div', {cls:'panel'}, [el('h2', {text:g.title}), el('p', {cls:'dim', text:g.about})]
      .concat(rows.map(row))));
  });
  var ban = $('banners'); ban.textContent = '';
  if(state.ignored){
    ban.appendChild(el('div', {cls:'banner bad'}, [el('strong', {text:'Saved values are being ignored. '}),
      'BB_IGNORE_UI_SETTINGS is set on this container, so nothing saved on this page applied at the last start. Remove the variable and restart to use them again.']));
  }
  if(state.pending){
    ban.appendChild(el('div', {cls:'banner'}, [el('strong', {text:'A restart is needed. '}),
      state.settings.filter(function(s){ return s.pending; }).length + ' setting(s) differ from what the container is running.']));
  }
  if(!state.running_with_record){
    ban.appendChild(el('div', {cls:'banner'}, ['This container did not start through the image entrypoint, so what it is running is read from its environment, and values saved here will not apply. Start it from the image\'s default command.']));
  }
  if(state.dropped && state.dropped.length){
    ban.appendChild(el('div', {cls:'banner bad'}, [el('strong', {text:'Skipped stored values: '}), state.dropped.join('; ')]));
  }
  if(state.warnings && state.warnings.length){
    var ul = el('ul', {}, state.warnings.map(function(w){ return el('li', {text:w}); }));
    ban.appendChild(el('div', {cls:'banner'}, [el('strong', {text:'Worth knowing'}), ul]));
  }
  $('restart').hidden = !state.restart_possible;
  if(LAST_UPD) pinHelpers(LAST_UPD.client);
  refreshSave();
}

function refreshSave(){ $('save').disabled = Object.keys(DIRTY).length === 0; }

function save(changes){
  say('Saving...');
  return post('manage/container', {changes: changes}).then(function(res){
    if(!res.ok){ say(res.body.error || 'Could not save.', 'bad'); return; }
    render(res.body);
    say('Saved. It applies when the container next starts.', 'good');
  });
}

$('save').addEventListener('click', function(){ save(DIRTY); });
$('restart').addEventListener('click', function(){
  var warn = 'The container will stop now. It comes back by itself only if it has a restart policy ' +
    '(the template sets "unless-stopped"); otherwise start it again from the Docker tab. Backups resume once it is up. Continue?';
  if(!confirm(warn)) return;
  post('manage/container/restart', {}).then(function(res){
    if(!res.ok){ say(res.body.error || 'Could not restart.', 'bad'); return; }
    say('Restarting... this page will reconnect when the container is back.', 'good');
    var tries = 0, t = setInterval(function(){
      tries++;
      fetch('manage/container', {cache:'no-store'}).then(function(r){ if(r.ok && tries > 3){ clearInterval(t); location.reload(); } }).catch(function(){});
      if(tries > 90) clearInterval(t);
    }, 3000);
  });
});

// ---- updates ----
function ago(ts){
  if(!ts) return 'never';
  var s = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if(s < 90) return 'just now'; if(s < 5400) return Math.round(s/60) + ' minutes ago';
  if(s < 129600) return Math.round(s/3600) + ' hours ago'; return Math.round(s/86400) + ' days ago';
}
function updRow(label, info, runningText){
  var chip;
  if(!info) chip = el('span', {cls:'chip', text:'not checked yet'});
  else if(info.error && !info.note) chip = el('span', {cls:'chip bad', text:'check failed'});
  else if(info.update) chip = el('span', {cls:'chip pending', text:'update available'});
  else if(info.latest || info.available) chip = el('span', {cls:'chip good', text:'up to date'});
  else chip = el('span', {cls:'chip', text:'unknown'});
  var lines = [el('div', {}, [chip, ' ', runningText || ''])];
  if(info && info.note) lines.push(el('div', {cls:'dim', text: info.note}));
  if(info && info.error) lines.push(el('div', {cls:'dim', text:'Last attempt failed: ' + info.error}));
  return el('div', {cls:'upd'}, [el('div', {cls:'name', text:label}), el('div', {}, lines)]);
}
// Beside the client pin: one click to pin what is installed, or what Backblaze is serving now.
function pinHelpers(c){
  var input = document.querySelector('input[data-var="BACKBLAZE_VERSION"]');
  if(!input || !c) return;
  var old = document.getElementById('pin-helpers'); if(old) old.parentNode.removeChild(old);
  var box = el('div', {id:'pin-helpers', cls:'meta'});
  [['installed', c.installed], ['available', c.available]].forEach(function(p){
    if(!p[1]) return;
    box.appendChild(el('button', {cls:'link', text:'Use ' + p[0] + ' (' + p[1] + ')', onclick: function(){
      input.value = p[1]; input.dispatchEvent(new Event('input')); }}));
  });
  box.appendChild(el('button', {cls:'link', text:'Unpin (follow the update switches)', onclick: function(){
    input.value = ''; input.dispatchEvent(new Event('input')); }}));
  input.parentNode.insertBefore(box, input.nextSibling);
}
function renderUpdates(u){
  var r = u.result || {};
  LAST_UPD = r;
  pinHelpers(r.client);
  $('upd-intro').textContent = u.enabled
    ? 'Checked every ' + u.interval_hours + ' hour(s) against Docker Hub (' + u.repo + '), WineHQ and Backblaze. Only version numbers are fetched.'
    : 'Update checks are switched off (Update notices -> Check for updates).';
  var rows = $('upd-rows'); rows.textContent = '';
  var img = r.image, w = r.wine, c = r.client;
  rows.appendChild(updRow('Container image', img, img && img.running ? 'running ' + img.running + (img.channel ? ' (:' + img.channel + ')' : '') : ''));
  rows.appendChild(updRow('Wine', w, w && w.running ? 'running ' + w.running + (w.flavour === 'patched' ? ' (patched)' : ' (WineHQ stable)') : ''));
  rows.appendChild(updRow('Backblaze client', c, c ? 'installed ' + (c.installed || 'not yet') + (c.available ? ', available ' + c.available : '') : ''));
  $('upd-when').textContent = r.at ? 'Last checked ' + ago(r.at) : '';
}
$('upd-check').addEventListener('click', function(){
  $('upd-check').disabled = true; $('upd-when').textContent = 'Checking...';
  post('manage/updates/check', {}).then(function(res){
    $('upd-check').disabled = false;
    if(res.ok) renderUpdates(res.body); else $('upd-when').textContent = res.body.error || 'Check failed.';
  });
});

// Digits switch the shell's tabs from inside this frame. The shell owns them.
document.addEventListener('keydown', function(e){
  var tg = e.target && e.target.tagName;
  if(tg === 'INPUT' || tg === 'SELECT' || tg === 'TEXTAREA' || e.ctrlKey || e.metaKey || e.altKey) return;
  if(e.key >= '1' && e.key <= '7' && window.parent !== window) window.parent.postMessage({bb64: 'tab', n: Number(e.key)}, location.origin);
});
try {
  var t = localStorage.getItem('bbMonitorTheme');
  if(t) document.documentElement.setAttribute('data-theme', t);
} catch(e){}

get('manage/container').then(render);
get('manage/updates').then(renderUpdates);
})();
</script>
</body></html>
"""


def html(theme_css, page_css):
    """The page with the monitor's theme and shared CSS spliced in."""
    return PAGE.replace("@@THEME_CSS@@", theme_css).replace("@@PAGE_CSS@@", page_css)
