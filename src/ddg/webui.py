# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The window: a tiny local web page (standard library only, no tkinter).

Bound to 127.0.0.1 only. Every request must carry a random token and a local
Host header, so other websites you have open cannot drive it.
"""
from __future__ import annotations

import json
import os
import mimetypes
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import __build_date__, __version__, core, gamemode, osk, sound
from . import library as steam
from pathlib import Path

from . import airlock, zombie
from .jobs import RUNNER

ASSETS = Path(__file__).resolve().parent / "assets"

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0a0d1f"><link rel="icon" href="/assets/icon.png">
<title>Deck Deck Goose __VERSION__ (Built __BUILD__)</title>
<style>
:root{--bg:#0a0d1f;--bg2:#121735;--card:#141a38;--ink:#eaf2ff;--mute:#9aa8d0;--line:#27305e;--accent:#25c8ff;--accent2:#ff8a1f;--ok:#4ade80;--bad:#ff7b7b;--warn:#3a2a0a;--warnink:#ffcf66;--chip:#1c2348;--glow:0 0 18px rgba(37,200,255,.35)}
html{background:#0a0d1f}*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 500px at 50% -120px,#1b2760 0,var(--bg) 70%) fixed;color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.05rem;margin:0 0 .6em;color:var(--accent2)}h3{color:var(--accent);font-size:1rem}
.hero{display:block;width:100%;max-height:360px;object-fit:cover;object-position:50% 0;border-radius:14px;border:1px solid var(--line);box-shadow:var(--glow)}
#band{display:flex;align-items:center;gap:.6em;padding:.35em .8em;border-radius:8px;font-weight:700;font-size:.9rem;margin:10px 0;color:#0a0d1f;background:#9ca3af}
#band i{width:.8em;height:.8em;border-radius:50%;background:#0a0d1f;opacity:.55}
.sr{position:absolute;left:-9999px}
.wip{background:var(--warn);color:var(--warnink);padding:.5em .8em;border-radius:8px;font-weight:600;margin:.6em 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:.92rem}td,th{text-align:left;padding:.35em .4em;border-bottom:1px solid var(--line);vertical-align:top}
td.nw,th.nw{white-space:nowrap}code{overflow-wrap:anywhere}label.mg{display:flex;gap:.5em;align-items:flex-start;margin:.25em 0;overflow-wrap:anywhere}
th{color:var(--mute);font-weight:600}code{font-family:ui-monospace,monospace;font-size:.88em}
button,select,input[type=search]{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:.5em 1em;border-radius:8px}
button{cursor:pointer}select{max-width:100%}
button.primary{background:var(--accent);border-color:var(--accent);color:#04101f;font-weight:700;box-shadow:var(--glow)}
button.amber{background:#f5a623;border-color:#f5a623;color:#1a1000;font-weight:700}
button:disabled,select:disabled{opacity:.5;cursor:not-allowed}.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.ok{color:var(--ok)}.bad{color:var(--bad)}.mute{color:var(--mute)}small{color:var(--mute)}
.tabs{display:flex;gap:6px;margin:12px 0 4px;border-bottom:1px solid var(--line)}
.tabs button{border:0;border-bottom:3px solid transparent;border-radius:0;background:none;font-weight:600;color:var(--mute);padding:.5em 1em}
.tabs button.on{color:var(--ink);border-bottom-color:var(--accent2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.game{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.art{aspect-ratio:2/3;background:var(--chip);position:relative}
.art img{position:relative;z-index:1;width:100%;height:100%;object-fit:cover;display:block}
.art .ini{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:2.4rem;font-weight:800;color:#fff}
.meta{padding:10px 12px 0}.meta b{display:block;line-height:1.25}
.chips{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}.chip{background:var(--chip);border-radius:99px;padding:0 .55em;font-size:.78rem}
.chip.w{background:var(--warn);color:var(--warnink)}.chip.g{color:var(--ok)}
.acts{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px;margin-top:auto}
.acts select{width:100%;font-size:.88rem;padding:.4em}.acts .row{margin:0;gap:6px}.acts button{padding:.4em .5em;font-size:.86rem;flex:1 1 auto}
.bar{height:10px;background:var(--chip);border-radius:99px;overflow:hidden;margin:8px 0}.bar i{display:block;height:100%;background:var(--accent);width:0}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}.toolbar input[type=search]{flex:1;min-width:160px}
#modal{position:fixed;inset:0;background:rgba(2,4,14,.75);display:none;align-items:center;justify-content:center;padding:16px}
#modal div{background:var(--card);border-radius:12px;padding:18px;max-width:520px}
@media(max-width:600px){.fold{display:none}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.meta{padding:8px 8px 0}.acts{padding:8px}.acts button{font-size:.8rem;padding:.35em .3em}.chip{font-size:.72rem}}
footer{color:var(--mute);font-size:.85rem;padding:10px 0 30px}
#gmbar{display:none}
body.gm{font-size:19px}body.gm .hero{max-height:190px}body.gm main{max-width:1280px}body.gm .tabs,body.gm #tab-pc,body.gm .gm-hide{display:none!important}
body.gm #gmbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:6px 0}
body.gm button,body.gm select,body.gm input[type=search]{min-height:48px;font-size:1rem}
body.gm .acts button{font-size:.95rem;min-height:48px}body.gm .grid{grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}
body.gm input[type=checkbox]{width:28px;height:28px}
.note{background:var(--chip);border-radius:8px;padding:.4em .7em;font-size:.88rem}
</style></head><body><main>
<h1 class="sr">Deck Deck Goose</h1>
<img class="hero" src="/assets/hero.webp" alt="Deck Deck Goose: a cyborg goose carrying a Steam Deck">
<div id="band"><i></i><span id="bandtxt">Checking mode...</span></div>
<div id="gmbar"><b>Game Mode</b><button class="primary" id="backsteam">Back to Steam</button></div>
<div class="wip">WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN (__VERSION__)</div>
<div id="simnote" class="wip" hidden></div>
<div class="card" id="inst" hidden><h2>Install Deck Deck Goose on this handheld</h2>
<p id="insttxt" class="mute" style="margin-top:0"></p>
<div class="row"><button class="primary" id="instgo">Install</button><button id="instdel" hidden>Remove menu entry and tiles</button></div>
<small>No password needed; everything goes in your home folder. Steam closes for a moment while the tiles are added. Afterwards you can delete the download folder.</small></div>
<div class="card" id="jobcard" hidden><h2 id="jobtitle"></h2><div class="bar"><i id="jobbar"></i></div>
<div id="jobmsg" class="mute"></div><table id="jobsteps"></table>
<div class="row"><button id="jobcancel">Cancel</button><button id="jobclose" hidden>Close</button></div></div>
<nav class="tabs"><button data-tab="pc" class="on">This PC</button><button data-tab="games">Games</button><button data-tab="sound">Sound</button><button data-tab="airlock">Airlock</button><button data-tab="wake">Wake</button></nav>

<section id="tab-pc">
<p class="mute">Gives your file managers a normal-looking <b>This PC</b> folder: your disks by name, Users, Documents, Downloads and so on, with plain paths. It never touches the operating system part of the disk.</p>
<div class="card"><h2>1. Your disks</h2>
<table id="disks"><tr><td class="mute">Looking...</td></tr></table>
<small>The system drive is hidden on purpose. Unmounted disks can be mounted with one click.</small></div>
<div class="card"><h2>2. What the This PC folder will contain</h2>
<table id="preview"></table></div>
<div class="card"><h2>3. File managers installed as Flatpak</h2>
<div id="managers" class="mute">Looking...</div>
<small>Flatpak apps are sandboxed and cannot see other disks. Ticking one gives it access to your home and mounted disks (<code>flatpak override --user</code>). That makes its sandbox weaker, so only tick the file managers you use.</small></div>
<div class="row">
<button class="primary" id="install">Install / update</button>
<button id="check">Check</button><button id="remove">Remove everything</button>
<button id="quit">Quit</button></div>
<div class="card" id="resultsCard" style="display:none"><h2>Result</h2><table id="results"></table></div>
<div class="card"><h2>On-screen keyboard (Desktop Mode)</h2>
<div id="oskinfo" class="mute">Checking...</div>
<div class="row"><button id="oskinst" hidden>Install keyboard</button><button id="oskon">Turn on</button><button id="oskoff">Turn off</button></div>
<small>Turns on KDE's touch keyboard, so it pops up when you tap a text box with the touchscreen. Only changes a setting in your home folder.</small></div>
</section>

<section id="tab-games" hidden>
<div class="card"><h2>Steam</h2><div id="steaminfo" class="mute">Looking for Steam...</div></div>

<div class="toolbar"><input type="search" id="q" placeholder="Search games">
<label><input type="checkbox" id="showtools"> <span id="toolslabel">Show Proton and runtimes</span></label>
<select id="sort"><option value="name">Sort by name</option><option value="size">Sort by size</option></select>
<select id="algo" title="Compression method" hidden></select></div>
<div class="grid" id="games"></div>
<div class="card"><h2>Clear junk</h2>
<p class="mute" style="margin-top:0">Finds leftovers Steam does not clean up. Safe items are ticked; items that could hold saves or mods are not.</p>
<div class="row"><button id="scan">Scan for junk</button><button class="primary" id="clean" disabled>Clean selected</button><span id="junktotal" class="mute"></span></div>
<table id="junk"></table></div>
</section>

<section id="tab-sound" hidden>
<div class="card"><h2>Sound</h2>
<div id="sndinfo" class="mute">Checking...</div>
<div class="row"><button class="primary" id="sndfix" hidden>Fix quiet sound</button></div>
<h3>Volume</h3>
<div class="row" id="sndboost"><button data-boost="100">100%</button><button data-boost="115">115%</button><button data-boost="130">130%</button><button data-boost="150">150% (max)</button></div>
<small>Sets the volume of the output in use. Above 100% is a software boost: loud parts can distort or crackle, so go up a step at a time. Steam's and KDE's own sliders still work afterwards.</small>
<h3>Outputs</h3>
<table id="sndlist"></table>
<small>Picking the <i>Family 17h/19h</i> device directly is louder at the same slider position, but skips Bazzite's speaker sound processing (EQ), so it can sound thinner. Try <b>Fix quiet sound</b> first.</small>
<div class="row"><label><input type="checkbox" id="sndkeep"> Re-apply these at every login (Desktop and Game Mode)</label><button id="sndreset">Back to normal</button></div>
<table id="sndsteps"></table></div>
</section>

<section id="tab-airlock" hidden>
<div class="card"><h2>Airlock: a desktop inside Game Mode</h2>
<p class="mute" style="margin-top:0">Tap the <b>Airlock</b> tile in Steam's Game Mode and a KDE desktop opens in a window. Your game and Steam keep running underneath. Close the desktop (or press <b>Back to Game</b>) and you are straight back, with no logout and no reboot.</p>
<div class="note">Work in progress: written from documentation and tested only with stand-ins, not yet on a real handheld. Run <b>Check this device</b> first and send me the result.</div>
<div id="alinfo" class="mute" style="margin-top:8px">Checking...</div>
<div class="row"><button class="amber" id="alopen">Open Airlock</button><button id="alstop">Back to Game</button><button id="alprobe">Check this device</button><button id="alclean" title="Closes desktop programs an earlier Airlock left running">Close leftovers</button></div>
<h3>If the nested desktop can't open, it tries, in order</h3>
<ol id="alplan" class="mute"></ol>
<small>It never restarts your handheld by itself. The step that closes your game (real Desktop Mode) always asks first.</small>
<h3>Extras</h3>
<div class="row"><button id="alback">Add &ldquo;Back to Game&rdquo; to the desktop's menu</button>
<label><input type="checkbox" id="alsleep"> Close Airlock before sleep (asks for your password once)</label></div>
<small>Colour hooks: drop a script in <code>~/.config/deck-deck-goose/hooks/mode-change.d/</code> and it is called with <code>portal</code>, <code>desktop</code> or <code>game</code> (amber, blue, green), for example to colour the controller lights.</small>
<table id="alsteps"></table><pre id="alout" class="mute" style="white-space:pre-wrap;overflow-wrap:anywhere" hidden></pre></div>
</section>

<section id="tab-wake" hidden>
<div class="card"><h2><img id="zbimg" src="/assets/zombie-brain-green.png" alt="" style="height:28px;vertical-align:middle;margin-right:8px">Wake: Zombie-Deck</h2>
<p class="mute" style="margin-top:0">Brings a Bazzite / Nobara handheld properly back to life after sleep or hibernation: power limit, controller, Wi-Fi and fans restored, with a green brain on screen when it worked and a red one when it didn't. Zombie-Deck is its own small project that Deck Deck Goose carries along and installs for you.</p>
<div id="zband" style="padding:8px 12px;border-radius:8px;color:#fff;font-weight:600;background:#6b7280">Checking...</div>
<div id="zinfo" class="mute" style="margin-top:8px"></div>
<div class="row"><button class="amber" id="zinst">Install</button><button id="zonoff">Turn off</button><button id="zlog">Show log</button><button id="zrm">Remove</button></div>
<small>Install, remove and on/off ask for your password once (Desktop Mode only). Removing keeps your settings file. To test it for real, open Zombie-Deck's menu (<code>components/zombie-deck/Zombie-Deck-Linux.sh</code>) and choose the sleep test.</small>
<table id="zsteps"></table><pre id="zout" class="mute" style="white-space:pre-wrap;overflow-wrap:anywhere" hidden></pre></div>
</section>

<footer>v__VERSION__ - __BUILD__ &nbsp;|&nbsp; Copyright Matthew Armstrong. PolyForm Noncommercial 1.0.0, no warranty.</footer>
</main>
<div id="modal"><div><h2>Before you start</h2>
<p>This tool creates symlinks in one folder in your home, edits your file-manager sidebar bookmarks (backups kept), installs one background timer for your user, and, if you tick them, loosens the sandbox of chosen Flatpak apps. The Games tab moves, cleans and compresses Steam game files when you ask it to. It is a work in progress and comes with <b>no warranty</b>. Keep backups of anything you cannot lose.</p>
<label><input type="checkbox" id="agree"> I understand, carry on</label>
<div class="row"><button class="primary" id="go" disabled>Continue</button><button id="no">Close</button></div></div></div>
<script>
const TOKEN="__TOKEN__";const GM=__GM__;const PLATFORM="__PLATFORM__";const LINUX=PLATFORM==="linux";
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let gone=false;
function stopped(){if(gone)return;gone=true;document.body.innerHTML='<main><h1>Deck Deck Goose</h1><div class="card"><h2>Deck Deck Goose has stopped</h2><p>Its window was closed, or it stopped after this tab was left asleep for a long time. Nothing is lost: start it again to carry on.</p></div></main>';}
async function api(path,body){let r;try{r=await fetch(path,{method:body?'POST':'GET',headers:{'X-Token':TOKEN,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});}catch(e){stopped();throw e;}return r.json();}
function steps(list){$('resultsCard').style.display='block';$('results').innerHTML=rows(list);}
function rows(list){return list.map(s=>`<tr><td class="${s.ok?'ok':'bad'}">${s.ok?'&#10003;':'&#10007;'}</td><td>${esc(s.name)}</td><td class="mute">${esc(s.detail)}</td></tr>`).join('');}
async function load(){
 const s=await api('/api/status');
 $('disks').innerHTML='<tr><th>Name</th><th class="nw">Size</th><th class="nw">Type</th><th>Mounted at</th></tr>'+(s.disks.length?s.disks.map(d=>`<tr><td>${esc(d.name)}</td><td class="nw">${d.size}</td><td class="nw">${esc(d.fstype)}</td><td>${d.mounted?'<code>'+esc(d.mountpoint)+'</code>':`<button data-mount="${esc(d.dev)}">Mount</button>`}</td></tr>`).join(''):'<tr><td colspan="4" class="mute">No extra disks found. Only the system drive is present, which is hidden.</td></tr>');
 $('preview').innerHTML='<tr><th>Path</th><th>Really is</th></tr>'+s.preview.map(p=>`<tr><td>${esc(p.path)}</td><td><code>${esc(p.target)}</code></td></tr>`).join('');
 $('managers').innerHTML=s.managers.length?s.managers.map(m=>`<label class="mg"><input type="checkbox" class="mgr" value="${esc(m.id)}" ${m.granted?'checked':''}> ${esc(m.id)} ${m.granted?'<span class="ok">(access granted)</span>':''}</label>`).join(''):'None found. Native file managers (not Flatpak) need nothing.';
 document.querySelectorAll('[data-mount]').forEach(b=>b.onclick=async()=>{steps([await api('/api/mount',{dev:b.dataset.mount})]);load();});
}
async function act(path,body){document.querySelectorAll('#tab-pc button').forEach(b=>b.disabled=true);try{steps(await api(path,body));}finally{document.querySelectorAll('#tab-pc button').forEach(b=>b.disabled=false);await load();}}
$('install').onclick=()=>act('/api/install',{apps:[...document.querySelectorAll('.mgr:checked')].map(c=>c.value)});
$('check').onclick=()=>act('/api/check');$('remove').onclick=()=>{if(confirm('Remove the This PC folder shortcuts, bookmarks and timer?'))act('/api/uninstall');};
$('quit').onclick=async()=>{const r=await api('/api/quit',{});if(r.error){alert(r.error);return;}document.body.innerHTML='<main><p>Closed. You can close this tab.</p></main>';};
$('agree').onchange=()=>$('go').disabled=!$('agree').checked;
$('go').onclick=()=>{try{localStorage.setItem('ddg-ok','1')}catch(e){}$('modal').style.display='none';};
$('no').onclick=()=>$('quit').click();
let ok=false;try{ok=localStorage.getItem('ddg-ok')==='1'}catch(e){}
if(!ok)$('modal').style.display='flex';

// ---------------- tabs
function showTab(t){document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===t));
 $('tab-pc').hidden=t!=='pc';$('tab-games').hidden=t!=='games';$('tab-sound').hidden=t!=='sound'&&!GM;$('tab-airlock').hidden=t!=='airlock';$('tab-wake').hidden=t!=='wake';if(t==='sound'||GM)snd();if(t==='airlock')al();if(t==='wake')wk();try{localStorage.setItem('ddg-tab',t)}catch(e){}
 if(t==='games'&&!G)loadGames();}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));

// ---------------- games
let G=null,busy=false;
function algo(){return $('algo').value||'';}
const hue=s=>{let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))%360;return h;};
const gb=b=>b>=1e9?(b/1e9).toFixed(1)+' GB':b>=1e6?Math.round(b/1e6)+' MB':Math.round(b/1e3)+' kB';
const need=()=>LINUX?'Needs a btrfs drive':PLATFORM==='mac'?'Compression is not available on macOS':'Needs an NTFS drive';
function est(g){const e=g.estimate;if(!e||g.compressed)return '';if(e.directstorage)return '<span class="chip w">DirectStorage: leave uncompressed</span>';const c=e.verdict==='worth it'?'g':e.verdict==='small gain'?'':'w';
 return `<span class="chip ${c}" title="estimated ${e.date}; already compressed by the drive: ${gb(e.already)}">~${gb(e.saved)} (${Math.round(e.pct)}%) &middot; ${esc(e.verdict)}</span>`;}
const initials=n=>n.split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase();
async function loadGames(){G=await api('/api/games');renderSteam();renderGames();}
function renderSteam(){
 const s=G;if(s.busy){$('steaminfo').textContent='Working... the list refreshes when the job finishes.';return;}
 if(!s.roots.length){$('steaminfo').innerHTML='Steam was not found '+(LINUX?'in your home folder':'on this '+(PLATFORM==='mac'?'Mac':'PC'))+'. Start Steam once, then press <button id="rescan">Look again</button>.';$('rescan').onclick=loadGames;return;}
 let h=s.roots.map(r=>`Steam: <code>${esc(r.path)}</code> ${r.flatpak?'(Flatpak)':''}`).join('<br>');
 $('simnote').hidden=!s.simulated;$('simnote').textContent='Simulated '+(s.platform_name||'')+' (test mode): nothing here is your real computer.';
 if(!LINUX){$('toolslabel').textContent='Show Steam tools';if(!$('algo').options.length&&(s.algorithms||[]).length){$('algo').hidden=false;$('algo').innerHTML=s.algorithms.map(a=>`<option value="${a}" ${a===s.default_algo?'selected':''}>Compression: ${a}${a===s.default_algo?' (recommended)':a==='LZX'?' (smallest, slowest)':a==='XPRESS4K'?' (fastest)':''}</option>`).join('');}}
 if(GM)h+='<div class="note">Game Mode: Steam stays open here, so Estimate, Compress and the safe junk items work, but moving does not. To move a game in Game Mode use Steam: Settings &gt; Storage.</div>';
 else h+=s.running?`<div class="row"><span class="bad">Steam is running. Close it before moving${LINUX?'. (Estimate, Compress and the safe junk items work with Steam open.)':', cleaning or compressing.'}</span><button id="closesteam">Close Steam</button></div>`:'<div class="ok">Steam is closed: ready.</div>';
 const gm=s.gamemode||{};
 if(!GM&&LINUX)h+=`<div class="row gm-hide"><span>Game Mode tiles: ${gm.installed?'<span class="ok">added</span> (Library &gt; Non-Steam: Deck Deck Goose and Airlock)':'not added'}${gm.browser?'':' <span class="bad">(needs a browser such as Firefox)</span>'}</span>
  <button id="gmadd" ${gm.browser?'':'disabled'}>${gm.installed?'Update Game Mode tiles':'Add to Game Mode'}</button>${gm.installed?'<button id="gmdel">Remove tiles</button>':''}</div>`;
 h+=(s.recovered||[]).map(x=>`<div class="${x.ok?'ok':'bad'}">${esc(x.name)}: ${esc(x.detail)}</div>`).join('');
 h+='<table style="margin-top:8px"><tr><th>Library drive</th><th class="fold">Folder</th><th class="nw">Free</th><th class="nw">Type</th><th>Compression</th></tr>'+s.libraries.map(l=>`<tr><td>${esc(l.drive)}</td><td class="fold"><code>${esc(l.path)}</code></td><td class="nw">${esc(l.free)}</td><td class="nw">${esc(l.fstype)}</td><td>${!l.available?'<span class="bad">not '+(LINUX?'mounted':'connected')+'</span>':l.can_compress?(l.compress?'drive compresses new files ('+esc(l.compress)+')':'available'):'<span class="mute">not available</span>'}</td></tr>`).join('')+'</table>';
 $('steaminfo').innerHTML=h;
 if($('gmadd'))$('gmadd').onclick=()=>{if(confirm('Add the Deck Deck Goose and Airlock tiles to Steam so they open in Game Mode?\n\nSteam is closed for a moment while its shortcut list is updated, then started again.'))startJob('/api/gamemode/install',{});};
 if($('gmdel'))$('gmdel').onclick=()=>{if(confirm('Remove the Deck Deck Goose and Airlock tiles from Steam? Steam is closed for a moment.'))startJob('/api/gamemode/remove',{});};
 if($('closesteam'))$('closesteam').onclick=async()=>{$('closesteam').disabled=true;$('closesteam').textContent='Closing...';const r=await api('/api/steam/close',{});if(!r.ok)alert(r.detail);loadGames();};
}
function renderGames(){
 const q=$('q').value.toLowerCase(),tools=$('showtools').checked;
 let list=G.games.filter(g=>(tools||!g.tool)&&g.name.toLowerCase().includes(q));
 if($('sort').value==='size')list.sort((a,b)=>b.bytes-a.bytes);
 $('games').innerHTML=list.length?list.map(g=>{
  const opts=g.targets.map(t=>`<option value="${esc(t.path)}" title="${esc(t.path)}" ${t.ok?'':'disabled'}>${esc(t.label)}${t.ok?'':' ('+esc(t.why.split('.')[0])+')'}</option>`).join('');
  const anyok=g.targets.some(t=>t.ok);
  return `<article class="game" data-id="${g.appid}">
  <div class="art"><div class="ini" style="background:hsl(${hue(g.name)} 45% 38%)">${esc(initials(g.name))}</div>${g.art?`<img loading="lazy" alt="" src="/api/art/${g.appid}?k=${TOKEN}" onerror="this.remove()">`:''}</div>
  <div class="meta"><b>${esc(g.name)}</b><small>${esc(g.size)} &middot; ${esc(g.drive)}</small>
  <div class="chips">${g.state_text?`<span class="chip w">${esc(g.state_text)}</span>`:''}${g.compressed?`<span class="chip g">compressed${g.compressed.saved?': saved '+gb(g.compressed.saved):''}</span>`:''}${g.tool?'<span class="chip">tool</span>':''}${est(g)}</div></div>
  <div class="acts">${GM?'<div class="note">Move: Steam &gt; Settings &gt; Storage</div>':`<select class="target" ${anyok&&!g.state_text?'':'disabled'}>${opts||'<option>No other drive found</option>'}</select>`}
  <div class="row">${GM?'':`<button class="mv primary" ${anyok&&!g.state_text&&!G.running?'':'disabled'}>Move</button>`}
  <button class="es" ${g.can_compress&&!g.compressed&&!g.state_text?'':'disabled'} title="${g.can_compress?'Read-only check: how much would compressing save?':need()}">Estimate</button>
  <button class="cp" ${g.can_compress&&!g.compressed&&!g.state_text&&g.estimate&&g.estimate.verdict!=='not worth it'&&!g.estimate.directstorage?'':'disabled'} title="${!g.can_compress?need():!g.estimate?'Press Estimate first':g.estimate.directstorage?'DirectStorage game: leave uncompressed':g.estimate.verdict==='not worth it'?'Would save too little':LINUX?'Compress with btrfs zstd':'Compress with Windows compression'}">Compress</button>
  ${g.compressed&&!LINUX?`<button class="un" ${G.running?'disabled':''}>Uncompress</button>`:''}</div></div></article>`;}).join(''):'<p class="mute">No games found.</p>';
 document.querySelectorAll('.game').forEach(card=>{
  const g=G.games.find(x=>x.appid===card.dataset.id);
  if(card.querySelector('.mv'))card.querySelector('.mv').onclick=()=>{const sel=card.querySelector('.target');const t=g.targets.find(x=>x.path===sel.value);
   if(!t||!t.ok)return;if(confirm(`Move ${g.name} (${g.size}) to ${t.path}?\n\n${LINUX?'Its Proton saves, shader cache and workshop items go with it.':'Its shader cache and workshop items go with it. Saves outside the game folder are not affected.'} Steam must stay closed until this finishes.`))startJob('/api/games/move',{appid:g.appid,target:t.path});};
  card.querySelector('.es').onclick=()=>startJob('/api/games/estimate',{appid:g.appid,algo:algo()});
  card.querySelector('.cp').onclick=()=>{if(confirm(`Compress ${g.name} with ${LINUX?'btrfs (zstd)':algo()}? Estimated saving: ${gb(g.estimate.saved)}. Files that would not shrink are skipped.\n\n${LINUX?'The game still runs normally. If you keep btrfs snapshots of this drive, compressing can use MORE space until old snapshots are deleted.':'The game still runs normally. Files Steam later updates are stored uncompressed again.'}`))startJob('/api/games/compress',{appid:g.appid,algo:algo()});};
  const un=card.querySelector('.un');if(un)un.onclick=()=>{if(confirm(`Store ${g.name} uncompressed again? This uses about ${gb((g.compressed||{}).saved||0)} more space.`))startJob('/api/games/uncompress',{appid:g.appid});};
 });
}
['q','showtools','sort'].forEach(id=>$(id).addEventListener('input',()=>G&&renderGames()));
async function startJob(path,body){const r=await api(path,body);if(r.error){alert(r.error);return;}pollJob();}
async function pollJob(){
 const j=await api('/api/job');if(!j||!j.id)return;
 $('jobcard').hidden=false;$('jobtitle').textContent=j.title;
 const pct=j.total?Math.min(100,Math.round(100*j.done/j.total)):(j.state==='running'?5:100);
 $('jobbar').style.width=pct+'%';$('jobmsg').textContent=j.message+(j.total&&j.state==='running'?` (${pct}%)`:'');
 $('jobsteps').innerHTML=rows(j.steps);$('jobcancel').hidden=j.state!=='running';$('jobclose').hidden=j.state==='running';
 busy=j.state==='running';
 if(busy)setTimeout(pollJob,700);else{loadGames();if(lastScan)scan();}
}
$('jobcancel').onclick=()=>api('/api/job/cancel',{});$('jobclose').onclick=()=>{$('jobcard').hidden=true;};
let lastScan=null;
async function scan(){$('scan').disabled=true;$('scan').textContent='Scanning...';lastScan=await api('/api/junk/scan',{});$('scan').disabled=false;$('scan').textContent='Scan again';
 $('junk').innerHTML=lastScan.length?'<tr><th></th><th>What</th><th class="nw">Size</th></tr>'+lastScan.map((d,i)=>{const held=G&&G.running&&!d.steam_ok;return `<tr><td><input type="checkbox" class="jk" data-i="${i}" ${d.default&&!held?'checked':''} ${held?'disabled':''}></td><td>${esc(d.label)}${held?' <small class="mute">(close Steam first)</small>':''}<br><small><code>${esc(d.path)}</code></small>${d.warning?`<br><small class="${d.category==='orphan-prefix'?'bad':''}">${esc(d.warning)}</small>`:''}</td><td class="nw">${esc(d.human)}</td></tr>`;}).join(''):'<tr><td class="ok">Nothing to clean.</td></tr>';
 document.querySelectorAll('.jk').forEach(c=>c.onchange=jtotal);jtotal();}
function jtotal(){const sel=[...document.querySelectorAll('.jk:checked')].map(c=>lastScan[+c.dataset.i]);
 const b=sel.reduce((a,d)=>a+d.size,0);$('junktotal').textContent=sel.length?`${sel.length} selected, about ${b>=1e9?(b/1e9).toFixed(1)+' GB':b>=1e6?Math.round(b/1e6)+' MB':Math.max(1,Math.round(b/1e3))+' kB'}`:'';$('clean').disabled=!sel.length;}
$('scan').onclick=scan;
$('clean').onclick=()=>{const ids=[...document.querySelectorAll('.jk:checked')].map(c=>lastScan[+c.dataset.i].id);
 if(confirm(`Delete ${ids.length} item(s)? This cannot be undone.`))startJob('/api/junk/clean',{ids});};

async function osk(){const o=await api('/api/osk/status');
 $('oskinfo').innerHTML=!o.keyboard?'<span class="bad">No KDE touch keyboard is installed</span> (looked for Plasma Keyboard and Maliit). '+(o.can_install?'Install keyboard adds <code>plasma-keyboard</code> with your system\'s package manager (<code>'+esc(o.package_manager)+'</code>; it asks for your password)'+(o.needs_reboot?', then needs a restart.':'.'):'Install the <code>plasma-keyboard</code> package with your software centre.')+' Steam\'s own keyboard still works while Steam is open.'
  :o.enabled?'<span class="ok">On</span> ('+esc(o.label)+'). Tap a text box with the touchscreen to type. If nothing appears, log out and back in once.':'Off. '+esc(o.label)+' (<code>'+esc(o.keyboard)+'</code>) is installed and ready.';
 $('oskinst').hidden=!o.can_install;$('oskon').disabled=!o.keyboard||o.enabled;$('oskoff').disabled=!o.enabled;}
$('oskinst').onclick=async()=>{const b=$('oskinst');b.disabled=true;b.textContent='Installing... (check for a password window)';
 try{steps(await api('/api/osk/install',{}));}finally{b.disabled=false;b.textContent='Install keyboard';osk();}};
$('oskon').onclick=async()=>{steps(await api('/api/osk/enable',{}));osk();};
$('oskoff').onclick=async()=>{steps(await api('/api/osk/disable',{}));osk();};
async function snd(){const o=await api('/api/sound/status');
 if(o.error){$('sndinfo').innerHTML='<span class="bad">'+esc(o.error)+'</span>';$('sndlist').innerHTML='';return;}
 let h=o.default?'In use: <b>'+esc(o.default_desc)+'</b> at '+o.volume+'%.':'<span class="bad">No output is in use.</span>';
 if(o.processing)h+=o.can_fix?' This is a sound-processing (EQ) output that plays through the built-in speakers, and the two volumes multiply. '+(o.fixed?'<span class="ok">The speakers underneath are at 100%.</span>':'<span class="bad">The speakers underneath are below 100%, which makes everything quiet.</span> Press <b>Fix quiet sound</b>.'):' This is a sound-processing output.';
 $('sndinfo').innerHTML=h;$('sndfix').hidden=!o.can_fix;$('sndfix').disabled=o.fixed;
 $('sndlist').innerHTML='<tr><th>Output</th><th class="nw">Volume</th><th></th></tr>'+o.sinks.map(s=>{const tags=[s.name===o.default?'<span class="ok">in use</span>':'',s.processing?'sound processing':'',s.internal&&!s.hdmi?'built-in':'',s.hdmi?'HDMI / screen':'',s.bluetooth?'Bluetooth':''].filter(Boolean).join(', ');
  return `<tr><td>${esc(s.desc)}<br><small class="mute">${tags}</small></td><td class="nw">${s.volume}%${s.muted?' (muted)':''}</td><td>${s.name===o.default?'':`<button data-use="${esc(s.name)}">Use this</button>`}</td></tr>`;}).join('');
 document.querySelectorAll('#sndboost button').forEach(b=>b.classList.toggle('primary',+b.dataset.boost===o.volume));
 document.querySelectorAll('[data-use]').forEach(b=>b.onclick=()=>sndact('/api/sound/use',{name:b.dataset.use}));
 $('sndkeep').checked=o.keep;}
async function sndact(path,body){document.querySelectorAll('#tab-sound button,#sndkeep').forEach(b=>b.disabled=true);
 try{$('sndsteps').innerHTML=rows(await api(path,body||{}));}finally{document.querySelectorAll('#tab-sound button,#sndkeep').forEach(b=>b.disabled=false);await snd();}}
$('sndfix').onclick=()=>sndact('/api/sound/fix');
document.querySelectorAll('#sndboost button').forEach(b=>b.onclick=()=>sndact('/api/sound/boost',{pct:+b.dataset.boost}));
$('sndkeep').onchange=()=>sndact('/api/sound/keep',{on:$('sndkeep').checked});
$('sndreset').onclick=()=>sndact('/api/sound/reset');
$('backsteam').onclick=async()=>{const r=await api('/api/quit',{});if(r.error){alert(r.error);return;}document.body.innerHTML='<main><p>Returning to Steam...</p></main>';};
if(GM){document.body.classList.add('gm');try{localStorage.setItem('ddg-ok','1')}catch(e){}$('modal').style.display='none';}

// ---------------- airlock + mode band
async function band(){try{const o=await api('/api/airlock/status');const i=o.indicator;$('band').style.background=i.colour;$('bandtxt').textContent=i.text;}catch(e){}}
async function al(){const [o,p]=await Promise.all([api('/api/airlock/status'),api('/api/airlock/plan')]);
 const open=o.portal_open;
 $('alinfo').innerHTML='You are in: <b>'+(o.session==='game'?'Game Mode':o.session==='desktop'?'Desktop Mode':'an unrecognised mode')+'</b>. '+(open?'<b class="ok">Airlock is open.</b>':'Airlock is closed.')+(o.last&&o.last.status==='failed'?' <span class="bad">Last try failed: see the log below.</span>':'');
 $('alopen').textContent=o.session==='game'?'Open Airlock':'Try Airlock from here';$('alopen').disabled=open;$('alstop').disabled=!open;
 const names={full:'Full KDE desktop in a window (game keeps running)',lite:'Just a file manager and terminal (uses less memory)',switch:'Real Desktop Mode (your game closes; asks first)',advise:'Show how to switch by hand'};
 $('alplan').innerHTML=p.modes.map(m=>'<li>'+esc(names[m]||m)+'</li>').join('')+p.notes.map(n=>'<li class="bad">'+esc(n)+'</li>').join('');
 try{const s=await api('/api/airlock/sleephook');$('alsleep').checked=s.installed;}catch(e){}}
async function alact(path,body){document.querySelectorAll('#tab-airlock button,#alsleep').forEach(b=>b.disabled=true);
 try{const r=await api(path,body||{});if(Array.isArray(r))$('alsteps').innerHTML=rows(r);return r;}finally{document.querySelectorAll('#tab-airlock button,#alsleep').forEach(b=>b.disabled=false);await al();band();}}
$('alopen').onclick=async()=>{const r=await alact('/api/airlock/start',{force:!GM&&true});if(r&&r.message)$('alsteps').innerHTML='<tr><td class="mute">'+esc(r.message)+'</td></tr>';};
$('alstop').onclick=()=>alact('/api/airlock/stop');
$('alclean').onclick=async()=>{const r=await alact('/api/airlock/cleanup');if(r&&r.message)$('alsteps').innerHTML='<tr><td class="mute">'+esc(r.message)+'</td></tr>';};
$('alback').onclick=()=>alact('/api/airlock/backentry');
$('alsleep').onchange=()=>alact('/api/airlock/sleephook',{on:$('alsleep').checked});
$('alprobe').onclick=async()=>{const r=await alact('/api/airlock/probe');if(r&&r.text){$('alout').hidden=false;$('alout').textContent=r.text+'\nSaved as deck-deck-goose-probe.txt in your home folder.';}};
// ---------------- wake (Zombie-Deck)
let ZB=null;
async function wk(){try{ZB=await api('/api/zombie/status');}catch(e){return;}
 const z=ZB,b=$('zband');if(!z.supported){b.textContent=z.message;b.style.background='#6b7280';['zinst','zonoff','zlog','zrm'].forEach(i=>$(i).hidden=true);return;}
 b.textContent=z.band.text;b.style.background=z.band.colour;$('zbimg').src='/assets/zombie-brain-'+(z.band.state==='fail'?'red':'green')+'.png';
 $('zinfo').innerHTML=z.installed?`Installed: version <b>${esc(z.version||'?')}</b>. Bundled here: <b>${esc(z.bundled||'?')}</b>.${z.update?' <span class="ok">An update is ready.</span>':''}${z.newer_installed?' A newer one than this copy is installed, so it is left alone.':''}${z.status&&z.status.sleeps!=null?` Slept ${z.status.sleeps}, woke ${z.status.wakes}.`:''}`:`Not installed. This copy is version <b>${esc(z.bundled||'?')}</b>.`;
 $('zinst').textContent=z.installed?(z.update?'Update to '+z.bundled:'Repair / reinstall'):'Install';$('zinst').hidden=false;$('zinst').disabled=!z.bundle_ok||z.newer_installed;
 $('zonoff').hidden=!z.installed;$('zonoff').textContent=z.status&&z.status.disabled?'Turn on':'Turn off';$('zlog').hidden=!z.installed;$('zrm').hidden=!z.installed;}
async function zact(path,body){document.querySelectorAll('#tab-wake button').forEach(b=>b.disabled=true);
 try{const r=await api(path,body||{});if(Array.isArray(r))$('zsteps').innerHTML=rows(r);return r;}finally{await wk();}}
$('zinst').onclick=()=>{if(confirm('Install Zombie-Deck?\n\nIt adds a few small system services and files under /usr/local and /etc, and asks for your password once. You can remove it here at any time.'))zact('/api/zombie/install');};
$('zrm').onclick=()=>{if(confirm('Remove Zombie-Deck? Your handheld goes back to stock sleep. Your settings file is kept.'))zact('/api/zombie/uninstall');};
$('zonoff').onclick=()=>zact('/api/zombie/enable',{on:!!(ZB&&ZB.status&&ZB.status.disabled)});
$('zlog').onclick=async()=>{const r=await api('/api/zombie/log');$('zout').hidden=false;$('zout').textContent=r.text||'(empty)';};
setInterval(()=>{if(!$('tab-wake').hidden&&!document.hidden&&!document.querySelector('#tab-wake button:disabled'))wk();},10000);
async function inst(){let s;try{s=await api('/api/app/status');}catch(e){return;}
 const need=!s.installed||s.update||!s.menu||!s.tiles;$('inst').hidden=GM||!need;if($('inst').hidden)return;
 $('insttxt').innerHTML=!s.installed?'Puts Deck Deck Goose in the app menu and adds the <b>Deck Deck Goose</b> and <b>Airlock</b> tiles to Steam\'s Game Mode, in one go.'
  :s.update?`Version <b>${esc(s.version)}</b> is installed; this is <b>${esc(s.running)}</b>. Press Update so the menu entry and the Game Mode tiles use this version.`
  :`Installed (${esc(s.version)}), but ${!s.menu?'the app-menu entry':''}${!s.menu&&!s.tiles?' and ':''}${!s.tiles?'the Game Mode tiles':''} ${(!s.menu&&!s.tiles)?'are':'is'} missing.`;
 if(!s.browser)$('insttxt').innerHTML+=' <span class="bad">No browser found for Game Mode: install Firefox first (Bazzite: Bazaar or Discover).</span>';
 $('instgo').textContent=!s.installed?'Install':s.update?'Update to '+s.running:'Repair';$('instdel').hidden=!s.installed;}
$('instgo').onclick=async()=>{if(!confirm('Install Deck Deck Goose?\n\n- program copy in ~/.local/share/deck-deck-goose\n- Deck Deck Goose in the app menu\n- Deck Deck Goose and Airlock tiles in Game Mode (Steam closes for a moment, then starts again)'))return;
 await startJob('/api/app/install',{});const t=setInterval(async()=>{if(!busy){clearInterval(t);inst();}},1500);};
$('instdel').onclick=async()=>{if(!confirm('Remove the app-menu entry and the Game Mode tiles? Steam closes for a moment.'))return;
 await startJob('/api/app/uninstall',{});const t=setInterval(async()=>{if(!busy){clearInterval(t);inst();}},1500);};
if(LINUX){band();setInterval(band,5000);inst();}
if(LINUX){load();osk();}else{document.querySelector('.tabs').style.display='none';$('band').style.display='none';}
setInterval(()=>api('/api/ping').catch(()=>{}),5000);
let tab='pc';try{tab=localStorage.getItem('ddg-tab')||'pc'}catch(e){}
showTab(GM||!LINUX?'games':tab);pollJob();
</script></body></html>
"""


IDLE_STOP = 15 * 60   # browsers slow down or freeze background tabs; be patient


def _token_ok(given: str, token: str) -> bool:
    return secrets.compare_digest(given.encode("utf-8", "replace"), token.encode("utf-8"))


class _Server(ThreadingHTTPServer):
    token = ""
    last_ping = time.time()
    daemon_threads = True


def _game_or_none(appid: str):
    return steam.find_game(str(appid)) if str(appid).isdigit() else None


class Handler(BaseHTTPRequestHandler):
    server: _Server  # type: ignore[assignment]

    def log_message(self, *args: object) -> None:  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: object, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode())

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            html = (PAGE.replace("__TOKEN__", self.server.token)
                    .replace("__GM__", "true" if (steam.is_linux() and gamemode.in_game_mode()) else "false")
                    .replace("__PLATFORM__", steam.platform())
                    .replace("__VERSION__", __version__).replace("__BUILD__", __build_date__))
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if url.path.startswith("/api/art/"):
            return self._art(url)
        if url.path.startswith("/assets/"):
            return self._asset(url.path[len("/assets/"):])
        self._api(None)

    def _asset(self, name: str) -> None:
        # the program's own pictures; no token needed so <img> works. Plain file names only.
        p = (ASSETS / name) if name and "/" not in name and "\\" not in name and not name.startswith(".") else None
        ctype = mimetypes.guess_type(name)[0] or ""
        if not p or not p.is_file() or not ctype.startswith("image/"):
            return self._json({"error": "not found"}, 404)
        self._send(200, p.read_bytes(), ctype)

    def _art(self, url: urllib.parse.SplitResult) -> None:
        k = urllib.parse.parse_qs(url.query).get("k", [""])[0]
        if not _token_ok(k, self.server.token):
            return self._json({"error": "bad token"}, 403)
        g = _game_or_none(url.path.rsplit("/", 1)[-1])
        p = steam.art_path(g.library.root, g.appid) if g else None
        if not p:
            return self._json({"error": "no artwork"}, 404)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if not ctype.startswith("image/"):
            return self._json({"error": "not an image"}, 404)
        self._send(200, p.read_bytes(), ctype)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._json({"error": "bad host"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        self._api(body if isinstance(body, dict) else {})

    def _start(self, title: str, fn) -> None:
        try:
            job = RUNNER.start(title, fn)
        except RuntimeError as e:
            return self._json({"error": str(e)})
        self._json(job.as_dict())

    def _api(self, body: Optional[dict]) -> None:
        if not _token_ok(self.headers.get("X-Token", ""), self.server.token):
            return self._json({"error": "bad token"}, 403)
        self.server.last_ping = time.time()
        path = urllib.parse.urlsplit(self.path).path
        b = body or {}
        try:
            if path == "/api/status":
                return self._json(core.status())
            if path == "/api/ping":
                return self._json({"ok": True})
            if path == "/api/check":
                return self._json([s.as_dict() for s in core.check()])
            if path == "/api/install":
                apps = [a for a in b.get("apps", []) if a in core.find_file_managers()]
                return self._json([s.as_dict() for s in core.install(apps=apps)])
            if path == "/api/uninstall":
                return self._json([s.as_dict() for s in core.uninstall()])
            if path == "/api/mount":
                return self._json(core.mount_disk(str(b.get("dev", ""))).as_dict())
            if path == "/api/quit":
                if RUNNER.busy():
                    return self._json({"error": "A job is still running. Wait for it or cancel it first."})
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            # ---- games
            if path == "/api/games":
                if RUNNER.busy():   # do not re-scan while files are moving
                    return self._json({"roots": [], "running": False, "libraries": [], "games": [],
                                       "recovered": [], "busy": True})
                st = steam.games_status()
                st.setdefault("platform", "linux")
                if steam.is_linux():
                    st["gamemode"] = gamemode.status()
                return self._json(st)
            if path == "/api/job":
                return self._json(RUNNER.current.as_dict() if RUNNER.current else {})
            if path == "/api/job/cancel":
                RUNNER.cancel()
                return self._json({"ok": True})
            if path == "/api/osk/status":
                return self._json(osk.status())
            if path == "/api/osk/install":
                return self._json([x.as_dict() for x in osk.install_keyboard()])
            if path == "/api/osk/enable":
                return self._json([x.as_dict() for x in osk.enable()])
            if path == "/api/osk/disable":
                return self._json([x.as_dict() for x in osk.disable()])
            if path == "/api/sound/status":
                return self._json(sound.status())
            if path == "/api/sound/fix":
                return self._json([x.as_dict() for x in sound.fix_quiet()])
            if path == "/api/sound/boost":
                try:
                    pct = int(b.get("pct", 100))
                except (TypeError, ValueError):
                    return self._json([core.Step("Volume", False, "not a number").as_dict()])
                return self._json([x.as_dict() for x in sound.boost(pct)])
            if path == "/api/sound/use":
                return self._json([x.as_dict() for x in sound.use_output(str(b.get("name", "")))])
            if path == "/api/sound/keep":
                return self._json([x.as_dict() for x in sound.keep(bool(b.get("on")))])
            if path == "/api/sound/reset":
                return self._json([x.as_dict() for x in sound.reset()])
            if path == "/api/airlock/status":
                return self._json(airlock.status())
            if path == "/api/airlock/plan":
                return self._json(airlock.plan())
            if path == "/api/airlock/start":
                airlock.spawn_start(force=bool(b.get("force")))
                return self._json({"ok": True, "message": "Starting... the colour bar at the top turns amber when it is open."})
            if path == "/api/airlock/cleanup":
                return self._json(airlock.cleanup())
            if path == "/api/airlock/stop":
                return self._json(airlock.stop())
            if path == "/api/airlock/backentry":
                return self._json([airlock.write_back_launcher().as_dict()])
            if path == "/api/airlock/sleephook":
                if "on" in b:
                    return self._json([x.as_dict() for x in airlock.sleep_hook(bool(b["on"]))])
                return self._json({"installed": airlock.sleep_hook_installed()})
            if path == "/api/zombie/status":
                return self._json(zombie.status())
            if path == "/api/zombie/install":
                return self._json([x.as_dict() for x in zombie.install()])
            if path == "/api/zombie/uninstall":
                return self._json([x.as_dict() for x in zombie.uninstall()])
            if path == "/api/zombie/enable":
                return self._json([x.as_dict() for x in zombie.set_enabled(bool(b.get("on")))])
            if path == "/api/zombie/log":
                return self._json({"text": zombie.log_text()})
            if path == "/api/airlock/probe":
                ok, text = airlock.probe()
                return self._json({"ok": ok, "text": text})
            if path == "/api/app/status":
                from . import appinstall
                return self._json(appinstall.status())
            if path == "/api/app/install":
                from . import appinstall
                return self._start("Installing Deck Deck Goose", lambda j: appinstall.install())
            if path == "/api/app/uninstall":
                from . import appinstall
                return self._start("Removing the menu entry and Game Mode tiles", lambda j: appinstall.uninstall())
            if path == "/api/gamemode/install":
                return self._start("Adding Deck Deck Goose and Airlock to Game Mode", lambda j: gamemode.install())
            if path == "/api/gamemode/remove":
                return self._start("Removing the Game Mode tile", lambda j: gamemode.remove())
            if path == "/api/steam/close":
                return self._json(steam.close_steam().as_dict())
            if path == "/api/junk/scan":
                return self._json(steam.scan_junk())
            if path == "/api/junk/clean":
                ids = [str(i) for i in b.get("ids", [])]
                return self._start("Cleaning junk", lambda j: steam.clean_junk(ids, j))
            if path in ("/api/games/move", "/api/games/compress", "/api/games/estimate", "/api/games/uncompress"):
                g = _game_or_none(str(b.get("appid", "")))
                if not g:
                    return self._json({"error": "game not found"})
                extra: tuple = ()
                if not steam.is_linux():                      # Windows has several compression methods
                    from .shelf import plat
                    algo = str(b.get("algo") or plat.DEFAULT_ALGO)
                    if plat.ALGORITHMS and algo not in plat.ALGORITHMS:
                        return self._json({"error": "unknown compression method"})
                    extra = (algo,) if plat.ALGORITHMS else ()
                if path.endswith("uncompress"):
                    if steam.is_linux():
                        return self._json({"error": "not available"})
                    return self._start("Uncompressing %s" % g.name, lambda j: steam.uncompress_game(g, j))
                if path.endswith("estimate"):
                    return self._start("Estimating %s" % g.name, lambda j: steam.estimate_game(g, j, *extra))
                if path.endswith("compress"):
                    return self._start("Compressing %s" % g.name, lambda j: steam.compress_game(g, j, *extra))
                target = str(b.get("target", ""))
                allowed = [t["path"] for t in steam.move_targets(g) if t["ok"]]
                if target not in allowed:
                    return self._json({"error": "That target is not available."})
                return self._start("Moving %s" % g.name, lambda j: steam.move_game(g, target, j))
        except Exception as e:  # show the problem in the window instead of a blank page
            return self._json([{"name": "Error", "ok": False, "detail": str(e)}], 200)
        self._json({"error": "not found"}, 404)


def serve(open_browser: bool = True, url_file: str = "") -> int:
    srv = _Server(("127.0.0.1", 0), Handler)
    srv.token = secrets.token_urlsafe(24)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("Deck Deck Goose %s - open %s (Ctrl+C to stop)" % (__version__, url))
    if url_file:     # the Game Mode launcher waits for this file, then opens the browser
        tmp = url_file + ".tmp"
        with open(tmp, "w") as f:
            f.write(url)
        os.replace(tmp, url_file)

    def watchdog() -> None:  # quit by itself once the tab has been closed
        while True:
            time.sleep(5)
            if time.time() - srv.last_ping > IDLE_STOP and not RUNNER.busy():
                srv.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if RUNNER.busy():
            print("A job is running; cancelling it safely...")
            RUNNER.cancel()
    RUNNER.wait()
    return 0
