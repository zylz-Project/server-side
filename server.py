#!/usr/bin/env python3
"""
Audio File Server — ESP32 多产品音频管理
  产品: 摇尾巴小熊猫 / 爬行大熊猫 / 大恐龙
  分类: 动物声音 / 环境声音
"""

import argparse, json, os
from flask import Flask, jsonify, request

app = Flask(__name__)
app.json.ensure_ascii = False

# --- 产品 ---
PRODUCTS = {
    "tail-wagging-panda": {"id":"tail-wagging-panda","name":"摇尾巴小熊猫","emoji":"🦊",
        "color":"#f08c40","bg":"rgba(240,140,64,0.12)","glow":"rgba(240,140,64,0.25)",
        "desc":"小熊猫 · 摇尾巴互动玩具","dir":"tail_wagging_panda"},
    "crawling-panda": {"id":"crawling-panda","name":"爬行大熊猫","emoji":"🐼",
        "color":"#34d399","bg":"rgba(52,211,153,0.12)","glow":"rgba(52,211,153,0.25)",
        "desc":"大熊猫 · 爬行互动玩具","dir":"crawling_panda"},
    "dinosaur": {"id":"dinosaur","name":"大恐龙","emoji":"🦖",
        "color":"#60a5fa","bg":"rgba(96,165,250,0.12)","glow":"rgba(96,165,250,0.25)",
        "desc":"恐龙 · 互动玩具","dir":"dinosaur"},
}

# --- 分类 ---
CATEGORIES = {
    "animal":  {"id":"animal",  "name":"动物声音", "emoji":"🐾"},
    "ambient": {"id":"ambient", "name":"环境声音", "emoji":"🌿"},
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio_files")
ALLOWED_EXT = ".opus"
MAX_FILE_SIZE = 32 * 1024 * 1024

def get_dir(pid, cid=None):
    p = PRODUCTS.get(pid)
    if not p: return None
    d = os.path.join(AUDIO_DIR, p["dir"])
    if cid:
        if cid not in CATEGORIES: return None
        d = os.path.join(d, cid)
    os.makedirs(d, exist_ok=True)
    return d

def ensure_dirs():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    for pid in PRODUCTS:
        for cid in CATEGORIES:
            get_dir(pid, cid)

ensure_dirs()

# --- Templates ---
def build_tabs():
    return "".join(
        f'<button class="tab-pill" data-product="{pid}" onclick="switchProduct(\'{pid}\')">'
        f'<span class="tab-emoji">{p["emoji"]}</span>'
        f'<span class="tab-name">{p["name"]}</span></button>'
        for pid, p in PRODUCTS.items()
    )

def build_meta():
    items = []
    for pid, p in PRODUCTS.items():
        items.append(f'"{pid}":{{name:"{p["name"]}",emoji:"{p["emoji"]}",'
            f'color:"{p["color"]}",bg:"{p["bg"]}",glow:"{p["glow"]}",desc:"{p["desc"]}"}}')
    return "{" + ",".join(items) + "}"

def build_esp32_opts():
    return "".join(f'<option value="{pid}">{p["emoji"]} {p["name"]}</option>' for pid, p in PRODUCTS.items())

# --- HTML ---
PAGE = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audio Hub</title><style>
:root{
  --bg-page:#f5f6fa;--bg-card:#fff;--bg-input:#f8f9fc;--bg-hover:#f0f2f8;
  --text-1:#1a1a2e;--text-2:#5a5a7a;--text-3:#9090b0;
  --border-1:#e8eaf0;--border-2:#d8dae5;
  --shadow-sm:0 1px 3px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.06);
  --shadow:0 4px 16px rgba(0,0,0,.06),0 1px 4px rgba(0,0,0,.04);
  --radius-sm:10px;--radius:14px;--radius-lg:18px;
  --ease:cubic-bezier(.4,0,.2,1);--ease-spring:cubic-bezier(.34,1.56,.64,1);
  --accent:#f08c40;--accent-light:rgba(240,140,64,.12);--accent-glow:rgba(240,140,64,.2);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Inter',system-ui,sans-serif;background:var(--bg-page);color:var(--text-1);min-height:100vh;line-height:1.55;-webkit-font-smoothing:antialiased}
body::before{content:'';position:fixed;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#f08c40,#34d399,#60a5fa);z-index:100}
.app{position:relative;z-index:1;max-width:780px;margin:0 auto;padding:24px 18px 52px}
.header{text-align:center;padding:20px 0 0}
.header-icon{font-size:38px;margin-bottom:8px}
.header h1{font-size:28px;font-weight:700;letter-spacing:-.5px}
.header .sub{font-size:13px;color:var(--text-3);margin-top:2px}

.tab-bar{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:24px 0 8px}
.tab-pill{display:flex;flex-direction:column;align-items:center;gap:6px;padding:16px 28px;background:var(--bg-card);border:2px solid var(--border-1);border-radius:16px;color:var(--text-2);font-size:15px;font-weight:500;cursor:pointer;font-family:inherit;transition:all .25s var(--ease);box-shadow:var(--shadow-sm);min-width:130px}
.tab-pill:hover{border-color:var(--border-2);color:var(--text-1);transform:translateY(-2px);box-shadow:var(--shadow)}
.tab-pill.active{color:#fff;font-weight:600;transform:translateY(-3px);box-shadow:0 6px 24px var(--accent-glow),var(--shadow)}
.tab-emoji{font-size:28px;line-height:1}
.tab-name{white-space:nowrap;font-size:14px}

.product-banner{display:flex;align-items:center;gap:18px;margin:18px 0;padding:20px 24px;background:var(--bg-card);border:1px solid var(--border-1);border-left:4px solid var(--accent);border-radius:var(--radius-lg);box-shadow:var(--shadow);transition:all .4s var(--ease)}
.product-banner-emoji{font-size:48px;line-height:1;animation:float 3s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
.product-banner-info{flex:1}
.product-banner-info .label{font-size:10px;text-transform:uppercase;letter-spacing:1.2px;color:var(--text-3);margin-bottom:2px;font-weight:600}
.product-banner-info .name{font-size:22px;font-weight:700}
.product-banner-info .desc{font-size:12px;color:var(--text-3);margin-top:2px}
.product-banner-stats{display:flex;flex-direction:column;align-items:center;gap:4px;padding:10px 18px;border-radius:var(--radius-sm);background:var(--bg-input);border:1px solid var(--border-1);min-width:72px}
.product-banner-stats .count{font-size:24px;font-weight:700;color:var(--accent);line-height:1}
.product-banner-stats .unit{font-size:10px;color:var(--text-3);text-align:center}

.cat-bar{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin:0 0 16px}
.cat-pill{display:flex;align-items:center;gap:6px;padding:8px 18px;background:var(--bg-card);border:1.5px solid var(--border-1);border-radius:50px;color:var(--text-2);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;transition:all .2s var(--ease);box-shadow:var(--shadow-sm)}
.cat-pill:hover{border-color:var(--border-2);color:var(--text-1)}
.cat-pill.active{color:var(--accent);font-weight:600;background:var(--accent-light);border-color:var(--accent);box-shadow:0 2px 8px var(--accent-glow)}

.card{background:var(--bg-card);border:1px solid var(--border-1);border-radius:var(--radius-lg);padding:22px;margin-bottom:14px;box-shadow:var(--shadow-sm);transition:all .4s var(--ease)}
.card.accent-top{border-top:3px solid var(--accent);box-shadow:var(--shadow)}
.card-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px}
.card-head h2{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}

.btn{display:inline-flex;align-items:center;gap:5px;padding:8px 18px;border:none;border-radius:8px;font-size:13px;font-weight:600;font-family:inherit;cursor:pointer;transition:all .2s var(--ease);white-space:nowrap}
.btn-primary{background:var(--accent);color:#fff;box-shadow:0 2px 10px var(--accent-glow)}
.btn-primary:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-ghost{background:var(--bg-card);color:var(--text-2);border:1px solid var(--border-1)}
.btn-ghost:hover{background:var(--bg-hover);color:var(--text-1);border-color:var(--border-2)}
.btn-sm{padding:5px 12px;font-size:11px;font-weight:500;border-radius:6px}
.btn-danger{background:#fff;color:#e5484d;border:1px solid #fecaca;font-size:11px;padding:5px 13px;font-weight:500}
.btn-danger:hover{background:#fef2f2;border-color:#fca5a5}

.upload-zone{border:2px dashed var(--border-2);border-radius:var(--radius);padding:32px 24px;text-align:center;cursor:pointer;transition:all .3s var(--ease);background:var(--bg-input);position:relative}
.upload-zone:hover{border-color:#c0c0d8;background:#eef0f8}
.upload-zone.drag-over{border-color:var(--accent)!important;border-style:solid;background:var(--accent-light);box-shadow:0 0 0 4px var(--accent-glow);transform:scale(1.01)}
.upload-zone-icon{width:54px;height:54px;border-radius:50%;background:var(--accent-light);display:flex;align-items:center;justify-content:center;margin:0 auto 10px;font-size:26px;color:var(--accent);transition:all .3s var(--ease)}
.upload-zone.drag-over .upload-zone-icon{animation:bounce .6s var(--ease-spring);box-shadow:0 0 20px var(--accent-glow)}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
.upload-zone h3{font-size:15px;font-weight:600;margin-bottom:3px}
.upload-zone p{font-size:12px;color:var(--text-3)}
.upload-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer}

.selected-bar{display:none;align-items:center;gap:8px;flex-wrap:wrap;margin-top:14px;padding:12px;background:var(--bg-input);border:1px solid var(--border-1);border-radius:var(--radius-sm)}
.selected-bar.show{display:flex}
.file-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1}
.file-chip{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:50px;background:var(--bg-card);border:1px solid var(--border-1);font-size:11px;color:var(--text-2);animation:fadeIn .2s var(--ease)}
.file-chip .chip-remove{cursor:pointer;color:var(--text-3);font-weight:700}
.file-chip .chip-remove:hover{color:#e5484d}
@keyframes fadeIn{from{opacity:0;transform:scale(.9)}to{opacity:1;transform:scale(1)}}

.progress-wrap{display:none;margin-top:12px}.progress-wrap.show{display:block}
.progress-track{width:100%;height:5px;background:var(--border-1);border-radius:3px;overflow:hidden}
.progress-fill{height:100%;border-radius:3px;background:var(--accent);transition:width .3s var(--ease)}
.progress-info{font-size:11px;color:var(--text-3);margin-top:3px}

.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.file-card{display:flex;align-items:center;gap:12px;padding:12px 14px;background:var(--bg-input);border:1px solid var(--border-1);border-radius:var(--radius-sm);transition:all .25s var(--ease);animation:cardIn .35s var(--ease) backwards}
.file-card:hover{border-color:var(--accent);background:var(--bg-card);transform:translateY(-2px);box-shadow:var(--shadow)}
.file-card-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}
.file-card-body{flex:1;min-width:0}
.file-card-name{font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.file-card-meta{font-size:10px;color:var(--text-3);margin-top:2px}
@keyframes cardIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}

.empty-state{text-align:center;padding:40px 16px}
.empty-icon{width:68px;height:68px;border-radius:50%;background:var(--bg-input);border:1px solid var(--border-1);display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:30px}
.empty-state h3{font-size:14px;font-weight:600;color:var(--text-2);margin-bottom:3px}
.empty-state p{font-size:12px;color:var(--text-3);max-width:280px;margin:0 auto}

.summary-bar{display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px solid var(--border-1);font-size:12px;color:var(--text-3)}
.summary-stat{display:flex;align-items:center;gap:5px}
.summary-stat .val{color:var(--text-1);font-weight:600}

.toast-rack{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:380px}
.toast{display:flex;align-items:flex-start;gap:10px;padding:14px 16px;background:#fff;border:1px solid var(--border-1);border-radius:var(--radius-sm);box-shadow:0 12px 32px rgba(0,0,0,.1);font-size:13px;animation:toastIn .35s var(--ease-spring)}
.toast.success{border-left:3px solid #34d399}.toast.error{border-left:3px solid #e5484d}.toast.warning{border-left:3px solid #f59e0b}
.toast-icon{font-size:18px;flex-shrink:0}
.toast-body{flex:1}.toast-body .toast-title{font-weight:600;font-size:12px}.toast-body .toast-msg{color:var(--text-2);font-size:12px}
.toast-close{background:none;border:none;color:var(--text-3);cursor:pointer;font-size:16px;padding:0 2px;opacity:.5}
.toast-close:hover{opacity:1}.toast.leaving{animation:toastOut .25s var(--ease) forwards}
@keyframes toastIn{from{opacity:0;transform:translateX(60px) scale(.95)}to{opacity:1;transform:translateX(0) scale(1)}}
@keyframes toastOut{from{opacity:1;transform:translateX(0)}to{opacity:0;transform:translateX(60px)}}

.esp32-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.input-field{background:var(--bg-input);color:var(--text-1);border:1px solid var(--border-1);border-radius:8px;padding:9px 14px;font-family:inherit;font-size:13px;transition:all .2s var(--ease)}
.input-field:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-light)}
.input-field::placeholder{color:var(--text-3)}
.esp32-status{margin-top:10px;font-size:12px;color:var(--text-3)}
.esp32-chips{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
.esp32-chip{background:var(--bg-input);border:1px solid var(--border-1);border-radius:50px;padding:4px 12px;font-size:11px;color:var(--text-2)}
.action-row{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap}
@media(max-width:520px){
  .app{padding:14px 10px 40px}.card{padding:16px}.file-grid{grid-template-columns:1fr}
  .tab-pill{padding:12px 18px;min-width:100px}.tab-emoji{font-size:24px}
  .header h1{font-size:24px}.product-banner{padding:14px 16px}
  .product-banner-emoji{font-size:38px}.product-banner-info .name{font-size:18px}
}
</style></head><body><div class="app">

<header class="header"><div class="header-icon">🎵</div><h1>Audio Hub</h1><p class="sub">多产品音频管理中心 · 拖拽上传 · 自动同步</p></header>
<nav class="tab-bar" id="tabBar">__TABS__</nav>

<div class="product-banner" id="productBanner">
  <div class="product-banner-emoji" id="bannerEmoji">🎯</div>
  <div class="product-banner-info">
    <div class="label">CURRENT PRODUCT</div>
    <div class="name" id="bannerName">请选择产品</div>
    <div class="desc" id="bannerDesc">在上方 Tab 中选择要管理的产品</div>
  </div>
  <div class="product-banner-stats">
    <div class="count" id="bannerCount">-</div>
    <div class="unit" id="bannerBreakdown">个音频</div>
    <div class="unit" id="bannerSize" style="margin-top:2px;font-weight:600;color:var(--text-2)"></div>
  </div>
</div>

<div class="cat-bar" id="catBar">
  <button class="cat-pill active" data-category="animal" onclick="switchCategory('animal')">🐾 动物声音</button>
  <button class="cat-pill" data-category="ambient" onclick="switchCategory('ambient')">🌿 环境声音</button>
</div>

<div class="toast-rack" id="toastRack"></div>

<section class="card accent-top" id="uploadCard">
  <div class="card-head"><h2>📤 上传音频</h2><span style="font-size:11px;color:var(--text-3)">.opus · 最大 32MB</span></div>
  <div class="upload-zone" id="dropZone">
    <div class="upload-zone-icon" id="uploadIcon">📁</div><h3>拖拽 .opus 文件到此处</h3><p>或点击此区域选择文件</p>
    <input type="file" id="fileInput" accept=".opus" multiple>
  </div>
  <div class="selected-bar" id="selectedBar">
    <div class="file-chips" id="selectedChips"></div>
    <button class="btn btn-primary btn-sm" onclick="doUpload()">开始上传</button>
    <button class="btn btn-ghost btn-sm" onclick="clearSelection()">取消</button>
  </div>
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-info" id="progressInfo"></div>
  </div>
</section>

<section class="card">
  <div class="card-head"><h2>📂 音频文件</h2><button class="btn btn-ghost btn-sm" onclick="refreshFiles()">🔄 刷新</button></div>
  <div id="fileList"><div class="empty-state"><div class="empty-icon">🎯</div><h3>选择产品开始管理</h3><p>在上方 Tab 中选择产品</p></div></div>
  <div class="summary-bar" id="summaryBar" style="display:none"></div>
</section>

<section class="card">
  <div class="card-head"><h2>🔌 ESP32 设备</h2></div>
  <div class="esp32-row">
    <input class="input-field" id="esp32ip" placeholder="ESP32 IP 地址" style="max-width:170px">
    <select class="input-field" id="esp32product">__ESP32_OPTS__</select>
    <button class="btn btn-ghost btn-sm" onclick="loadEsp32Status()">查询</button>
  </div>
  <div class="esp32-status" id="esp32Status">输入 ESP32 IP 后查询 Flash 状态</div>
  <div class="esp32-chips" id="esp32FileChips"></div>
  <div class="action-row">
    <button class="btn btn-danger" onclick="esp32Erase()">擦除 ESP32 Flash</button>
    <button class="btn btn-danger" onclick="serverClear()">清空当前产品</button>
  </div>
</section>

</div><script>
var PRODUCTS = __META__;
var activeProduct = "";
var activeCategory = "animal";
var _loadId = 0;
var _loadTimer = 0;

function toast(t,m,ty){
  ty=ty||'info';var ic={success:'✅',error:'❌',warning:'⚠️',info:'ℹ️'};
  var el=document.createElement('div');el.className='toast '+ty;
  el.innerHTML='<span class="toast-icon">'+ic[ty]+'</span><div class="toast-body"><div class="toast-title">'+esc(t)+'</div>'+(m?'<div class="toast-msg">'+esc(m)+'</div>':'')+'</div><button class="toast-close" onclick="this.closest(\'.toast\').remove()">×</button>';
  document.getElementById('toastRack').appendChild(el);
  setTimeout(function(){if(el.parentElement){el.classList.add('leaving');setTimeout(function(){if(el.parentElement)el.remove();},250);}},4000);
}

function refreshFiles(){
  if(!activeProduct) return;
  var p = PRODUCTS[activeProduct]; if(!p) return;
  var id = ++_loadId;
  var list = document.getElementById('fileList');
  list.innerHTML = '<div class="empty-state"><div class="empty-icon">'+p.emoji+'</div><h3>加载中...</h3></div>';

  clearTimeout(_loadTimer);
  _loadTimer = setTimeout(function(){
    if(id === _loadId) list.innerHTML = '<div class="empty-state"><div class="empty-icon">⏰</div><h3>请求超时</h3><p>请刷新页面重试</p></div>';
  }, 10000);

  fetch('/api/files?product='+encodeURIComponent(activeProduct)+'&category='+activeCategory+'&_='+Date.now(), {cache:'no-store'})
  .then(function(r){ return r.json(); })
  .then(function(d){
    if(id !== _loadId) return;
    clearTimeout(_loadTimer);
    var files = d.files || [];
    var sum = document.getElementById('summaryBar');
    if(!files.length){
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">'+p.emoji+'</div><h3>'+p.name+' · 暂无音频</h3><p>拖拽 .opus 文件到上方区域开始上传</p></div>';
      sum.style.display = 'none';
    } else {
      var total = 0;
      var html = '<div class="file-grid">';
      for(var i = 0; i < files.length; i++){
        var f = files[i]; total += f.size;
        var sz = f.size > 1048576 ? (f.size/1048576).toFixed(1)+' MB' : (f.size/1024).toFixed(1)+' KB';
        html += '<div class="file-card"><div class="file-card-icon" style="background:'+p.bg+';color:'+p.color+'">🎵</div><div class="file-card-body"><div class="file-card-name" title="'+esc(f.name)+'">'+esc(f.name)+'</div><div class="file-card-meta">'+sz+' · .opus</div></div><button class="btn btn-sm" style="color:var(--text-3);background:transparent;border:1px solid var(--border-1)" onclick="doDelete(\''+escAttr(f.name)+'\')">🗑️</button></div>';
      }
      html += '</div>';
      list.innerHTML = html;
      sum.style.display = 'flex';
      sum.innerHTML = '<span class="summary-stat">📦 <span class="val">'+files.length+'</span> 个文件</span><span class="summary-stat">💾 当前分类 <span class="val">'+(total/1048576).toFixed(1)+'</span> MB</span>';
    }
    return fetch('/api/summary?product='+encodeURIComponent(activeProduct), {cache:'no-store'});
  })
  .then(function(r){ if(r) return r.json(); })
  .then(function(d){
    if(!d || id !== _loadId) return;
    document.getElementById('bannerCount').textContent = d.total;
    var parts = [];
    for(var cid in d.totals){ var t = d.totals[cid]; parts.push(t.emoji+' '+t.count); }
    document.getElementById('bannerBreakdown').textContent = parts.join('  ');
    var sz = d.total_size > 1048576 ? (d.total_size/1048576).toFixed(1)+' MB' : (d.total_size/1024).toFixed(1)+' KB';
    document.getElementById('bannerSize').textContent = '共 '+sz;
  })
  .catch(function(e){
    if(id !== _loadId) return;
    clearTimeout(_loadTimer);
    list.innerHTML = '<div class="empty-state"><div class="empty-icon">❌</div><h3>加载失败</h3><p>'+esc(String(e))+'</p></div>';
  });
}

function switchProduct(pid){
  if(activeProduct === pid) return;
  activeProduct = pid;
  var p = PRODUCTS[pid]; if(!p) return;
  var root = document.documentElement;
  root.style.setProperty('--accent', p.color);
  root.style.setProperty('--accent-light', p.bg);
  root.style.setProperty('--accent-glow', p.glow);
  var tabs = document.querySelectorAll('.tab-pill');
  for(var i = 0; i < tabs.length; i++){
    var is = tabs[i].dataset.product === pid;
    if(is){ tabs[i].classList.add('active'); tabs[i].style.background = p.color; tabs[i].style.borderColor = p.color; }
    else  { tabs[i].classList.remove('active'); tabs[i].style.background = ''; tabs[i].style.borderColor = ''; }
  }
  document.getElementById('bannerEmoji').textContent = p.emoji;
  document.getElementById('bannerName').textContent = p.name;
  document.getElementById('bannerDesc').textContent = p.desc;
  document.getElementById('bannerCount').textContent = '...';
  document.getElementById('uploadIcon').textContent = p.emoji;
  document.getElementById('uploadCard').style.borderTopColor = p.color;
  var cats = document.querySelectorAll('.cat-pill');
  for(var j = 0; j < cats.length; j++){ cats[j].classList.toggle('active', cats[j].dataset.category === activeCategory); }
  clearSelection();
  refreshFiles();
}

function switchCategory(cid){
  activeCategory = cid;
  var cats = document.querySelectorAll('.cat-pill');
  for(var i = 0; i < cats.length; i++){ cats[i].classList.toggle('active', cats[i].dataset.category === cid); }
  clearSelection();
  refreshFiles();
}

var selectedFiles = [];
(function(){
  var dz = document.getElementById('dropZone');
  dz.addEventListener('dragover', function(e){ e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', function(){ dz.classList.remove('drag-over'); });
  dz.addEventListener('drop', function(e){ e.preventDefault(); dz.classList.remove('drag-over'); addFiles(e.dataTransfer.files); });
  document.getElementById('fileInput').addEventListener('change', function(){ addFiles(this.files); this.value = ''; });
})();

function addFiles(files){
  if(!activeProduct){ toast('提示','请先选择产品','warning'); return; }
  for(var i = 0; i < files.length; i++){
    var f = files[i];
    if(!f.name.endsWith('.opus')){ toast('跳过','非 opus: '+f.name,'warning'); continue; }
    if(f.size > 32*1024*1024){ toast('过大', f.name, 'error'); continue; }
    var dup = false;
    for(var j = 0; j < selectedFiles.length; j++){ if(selectedFiles[j].name===f.name && selectedFiles[j].size===f.size){ dup=true; break; } }
    if(!dup) selectedFiles.push(f);
  }
  refreshSel();
}
function removeFile(i){ selectedFiles.splice(i,1); refreshSel(); }
function clearSelection(){ selectedFiles = []; refreshSel(); }
function refreshSel(){
  var bar = document.getElementById('selectedBar'), chips = document.getElementById('selectedChips');
  if(!selectedFiles.length){ bar.classList.remove('show'); return; }
  bar.classList.add('show');
  var h = '';
  for(var i = 0; i < selectedFiles.length; i++){ h += '<span class="file-chip">🎵 '+esc(selectedFiles[i].name)+'<span class="chip-remove" onclick="removeFile('+i+')">×</span></span>'; }
  chips.innerHTML = h;
}

function uploadOne(file){
  return new Promise(function(resolve){
    var fd = new FormData(); fd.append('file', file); fd.append('product', activeProduct); fd.append('category', activeCategory);
    var x = new XMLHttpRequest(); x.open('POST', '/api/upload');
    x.upload.onprogress = function(e){ if(e.lengthComputable){ document.getElementById('progressFill').style.width = (e.loaded/e.total*100).toFixed(0)+'%'; document.getElementById('progressInfo').textContent = (e.loaded/1024/1024).toFixed(1)+' / '+(e.total/1024/1024).toFixed(1)+' MB'; } };
    x.onload = function(){ resolve(x.status === 200); };
    x.onerror = function(){ resolve(false); };
    x.send(fd);
  });
}
async function doUpload(){
  if(!activeProduct){ toast('提示','请先选择产品','warning'); return; }
  if(!selectedFiles.length){ toast('提示','请先选择文件','warning'); return; }
  var w = document.getElementById('progressWrap'); w.classList.add('show');
  document.getElementById('progressFill').style.width = '0%';
  var ok = 0, fail = 0;
  for(var i = 0; i < selectedFiles.length; i++){
    document.getElementById('progressInfo').textContent = '上传中 ('+(i+1)+'/'+selectedFiles.length+'): '+selectedFiles[i].name;
    (await uploadOne(selectedFiles[i])) ? ok++ : (fail++, toast('失败', selectedFiles[i].name, 'error'));
  }
  w.classList.remove('show'); clearSelection();
  if(ok) toast('完成','成功 '+ok+' 个','success');
  refreshFiles();
}

async function doDelete(fn){
  if(!activeProduct) return;
  if(!confirm('确认删除「'+fn+'」？')) return;
  try{ var r = await fetch('/api/delete/'+encodeURIComponent(fn)+'?product='+encodeURIComponent(activeProduct)+'&category='+activeCategory, {method:'DELETE'}); r.ok ? toast('已删除',fn,'success') : toast('失败',await r.text(),'error'); } catch(e){ toast('错误',String(e),'error'); }
  refreshFiles();
}
async function serverClear(){
  if(!activeProduct) return;
  if(!confirm('确认清空「'+PRODUCTS[activeProduct].name+'」当前分类所有音频？不可撤销！')) return;
  try{ var r = await fetch('/api/clear?product='+encodeURIComponent(activeProduct)+'&category='+activeCategory, {method:'POST'}); toast('完成',await r.text(),'success'); } catch(e){ toast('错误',String(e),'error'); }
  refreshFiles();
}

var erasing = false;
async function esp32Erase(){
  if(erasing) return;
  var ip = document.getElementById('esp32ip').value.trim();
  if(!ip){ toast('提示','输入 ESP32 IP','warning'); return; }
  if(!confirm('确认擦除 ESP32 Flash？不可撤销！')) return;
  erasing = true; toast('进行中','正在擦除...','warning');
  try{ var r = await fetch('/api/proxy-flash-erase?ip='+encodeURIComponent(ip)); toast('完成',await r.text(),'success'); loadEsp32Status(); } catch(e){ toast('错误',String(e),'error'); }
  erasing = false;
}
async function loadEsp32Status(){
  var ip = document.getElementById('esp32ip').value.trim(); if(!ip) return;
  try{
    var r = await fetch('/api/proxy-flash?ip='+encodeURIComponent(ip)), j = await r.json();
    var st = document.getElementById('esp32Status'), ch = document.getElementById('esp32FileChips');
    if(j.error){ st.innerHTML = '<span style="color:#e5484d">❌ '+esc(j.error)+'</span>'; ch.innerHTML = ''; }
    else if(!j.count){ st.innerHTML = 'Flash 空'; ch.innerHTML = ''; }
    else { var t=0, h=''; for(var i=0;i<j.files.length;i++){ t+=j.files[i].size; h+='<span class="esp32-chip">📄 '+esc(j.files[i].name)+' ('+(j.files[i].size/1024).toFixed(1)+' KB)</span>'; } ch.innerHTML=h; st.innerHTML='<span style="color:#34d399">✅ '+j.count+' 个文件, '+(t/1048576).toFixed(1)+' MB / 32 MB</span>'; }
  } catch(e){ document.getElementById('esp32Status').innerHTML='<span style="color:#e5484d">❌ 无法连接</span>'; document.getElementById('esp32FileChips').innerHTML=''; }
}
setInterval(function(){ if(document.getElementById('esp32ip').value.trim()) loadEsp32Status(); }, 10000);

function esc(s){ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function escAttr(s){ return s.replace(/'/g,"\\'").replace(/"/g,'&quot;'); }

(function(){
  var first = document.querySelector('.tab-pill');
  if(first) switchProduct(first.dataset.product);
})();
</script></body></html>"""

@app.route("/")
def index():
    page = PAGE.replace("__TABS__", build_tabs())
    page = page.replace("__META__", build_meta())
    page = page.replace("__ESP32_OPTS__", build_esp32_opts())
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}

# --- API helpers ---
def _path():
    pid = request.args.get("product","").strip() or (request.form.get("product") or "").strip()
    cid = request.args.get("category","").strip() or (request.form.get("category") or "").strip()
    if not pid: return None,None,None,("Missing 'product'",400)
    if not cid: return None,None,None,("Missing 'category'",400)
    d = get_dir(pid, cid)
    if not d: return None,None,None,(f"Invalid product/category",400)
    return d,pid,cid,None

def nocache(r):
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return r

# --- API routes ---
@app.route("/api/summary")
def api_summary():
    pid = request.args.get("product","").strip()
    if not pid or pid not in PRODUCTS: return jsonify({"error":"Invalid product"}),400
    totals = {}
    for cid,c in CATEGORIES.items():
        d = get_dir(pid, cid)
        files = [f for f in os.listdir(d) if f.endswith(ALLOWED_EXT)]
        total_size = sum(os.path.getsize(os.path.join(d,f)) for f in files)
        totals[cid] = {"count":len(files),"size":total_size,"name":c["name"],"emoji":c["emoji"]}
    all_count = sum(v["count"] for v in totals.values())
    all_size = sum(v["size"] for v in totals.values())
    return nocache(jsonify({"product":pid,"totals":totals,"total":all_count,"total_size":all_size}))

@app.route("/api/files")
def api_files():
    """GET /api/files?product=xxx[&category=xxx] — 不指定category时返回所有分类合并列表"""
    pid = request.args.get("product","").strip()
    if not pid or pid not in PRODUCTS: return "Missing/invalid product",400
    cid = request.args.get("category","").strip()
    files = []
    if cid:
        d = get_dir(pid, cid)
        if not d: return "Invalid category",400
        for fn in sorted(os.listdir(d)):
            if fn.endswith(ALLOWED_EXT):
                files.append({"name":fn,"size":os.path.getsize(os.path.join(d,fn)),
                              "category":cid})
    else:
        # Merge all categories
        for cid2,c in CATEGORIES.items():
            d = get_dir(pid, cid2)
            for fn in sorted(os.listdir(d)):
                if fn.endswith(ALLOWED_EXT):
                    files.append({"name":fn,"size":os.path.getsize(os.path.join(d,fn)),
                                  "category":cid2})
    return nocache(jsonify({"product":pid,"category":cid or "all","files":files}))

def _send_opus_file(filepath, filename):
    """发送文件 — 使用 send_file，显式禁 conditional 避免 chunked"""
    from flask import send_file
    return send_file(filepath, mimetype="application/octet-stream",
                     download_name=filename, conditional=False)


@app.route("/api/download/<path:fn>")
def api_download(fn):
    pid = request.args.get("product","").strip()
    if not pid or pid not in PRODUCTS: return "Missing/invalid product",400
    cid = request.args.get("category","").strip()
    safe = os.path.basename(fn)
    if cid:
        d = get_dir(pid, cid)
        fp = os.path.join(d, safe) if d else ""
        if os.path.isfile(fp): return _send_opus_file(fp, safe)
    else:
        for cid2 in CATEGORIES:
            d = get_dir(pid, cid2)
            fp = os.path.join(d, safe)
            if os.path.isfile(fp): return _send_opus_file(fp, safe)
    return "Not Found",404

@app.route("/api/download-idx/<int:idx>")
def api_download_idx(idx):
    """GET /api/download-idx/N?product=xxx[&category=xxx] — 分块流式下载"""
    pid = request.args.get("product","").strip()
    if not pid or pid not in PRODUCTS: return "Missing/invalid product",400
    cid = request.args.get("category","").strip()
    files = []
    # Must match /api/files sort order: by category then by name within category
    if cid:
        d = get_dir(pid, cid)
        if not d: return "Invalid category",400
        files = sorted([f for f in os.listdir(d) if f.endswith(ALLOWED_EXT)])
    else:
        for cid2 in CATEGORIES:
            d = get_dir(pid, cid2)
            for fn in sorted(os.listdir(d)):
                if fn.endswith(ALLOWED_EXT):
                    files.append(fn)
        # NO final sort — keep category grouping same as /api/files
    if idx<0 or idx>=len(files): return "Index out of range",404
    if cid:
        fpath = os.path.join(d, files[idx])
    else:
        fpath = ""
        for cid2 in CATEGORIES:
            d = get_dir(pid, cid2)
            test = os.path.join(d, files[idx])
            if os.path.isfile(test): fpath = test; break
    if not fpath or not os.path.isfile(fpath): return "Not Found",404
    return _send_opus_file(fpath, files[idx])

@app.route("/api/upload", methods=["POST"])
def api_upload():
    d,pid,cid,err = _path()
    if err: return err
    f = request.files.get("file")
    if not f or not f.filename: return "No file",400
    fn = os.path.basename(f.filename)
    if not fn.endswith(ALLOWED_EXT): return "Only .opus allowed",400
    f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
    if size > MAX_FILE_SIZE: return "File too large",413
    f.save(os.path.join(d, fn))
    print(f"[UPLOAD] [{pid}/{cid}] {fn} ({size:,} bytes)")
    return "OK",200

@app.route("/api/delete/<path:fn>", methods=["DELETE"])
def api_delete(fn):
    d,pid,cid,err = _path()
    if err: return err
    fp = os.path.join(d, os.path.basename(fn))
    if not os.path.isfile(fp): return "Not Found",404
    size = os.path.getsize(fp); os.remove(fp)
    print(f"[DELETE] [{pid}/{cid}] {os.path.basename(fn)} ({size:,} bytes)")
    return "OK",200

@app.route("/api/clear", methods=["POST"])
def api_clear():
    d,pid,cid,err = _path()
    if err: return err
    n = 0
    for fn in os.listdir(d):
        if fn.endswith(ALLOWED_EXT): os.remove(os.path.join(d,fn)); n+=1
    print(f"[CLEAR] [{pid}/{cid}] {n} files")
    return f"已删除 {n} 个文件",200

@app.route("/api/proxy-flash-erase")
def api_proxy_flash_erase():
    ip = request.args.get("ip","").strip()
    if not ip: return "Missing ip",400
    try:
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(f"http://{ip}/api/flash/erase", method="POST"), timeout=10) as r:
            return r.read().decode()
    except Exception as e: return f"Error: {e}",502

@app.route("/api/proxy-flash")
def api_proxy_flash():
    ip = request.args.get("ip","").strip()
    if not ip: return jsonify({"error":"Missing ip"}),400
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://{ip}/api/flash/status", timeout=5) as r:
            return jsonify(json.loads(r.read().decode()))
    except Exception as e: return jsonify({"error":str(e)}),502

# --- Main ---
def main():
    global AUDIO_DIR
    default_dir = AUDIO_DIR
    p = argparse.ArgumentParser(description="Audio Hub")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--dir", default=default_dir)
    args = p.parse_args()
    AUDIO_DIR = os.path.abspath(args.dir)
    ensure_dirs()
    print("="*50)
    print("  Audio Hub — 多产品音频管理")
    print(f"  地址: http://{args.host}:{args.port}")
    print("-"*50)
    for pid,p in PRODUCTS.items():
        total=0; parts=[]
        for cid,c in CATEGORIES.items():
            d=get_dir(pid,cid); n=len([f for f in os.listdir(d) if f.endswith(ALLOWED_EXT)])
            total+=n; parts.append(f"{c['emoji']}{n}")
        print(f"  {p['emoji']} {p['name']:10s} → {total} 个 ({'  '.join(parts)})")
    print("="*50)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
