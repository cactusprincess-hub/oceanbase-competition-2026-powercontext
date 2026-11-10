import './style.css';
import { MODEL, modelKey } from '../shared/model.mjs';
import { retrievalMetrics, median, splitDocument } from './metrics.js';

document.querySelector('#app').innerHTML = `
  <aside><a class="brand" href="/">local<span>notes</span><i>✳</i></a>
    <p class="eyebrow">YOUR KNOWLEDGE, WITHIN REACH</p>
    <div class="rule"></div><h2>我的知识库</h2>
    <label for="collection">当前知识库</label><select id="collection"></select>
    <form id="create-form"><input id="new-name" aria-label="新知识库名称" placeholder="新的知识库 · 英文名" maxlength="40"><button aria-label="创建知识库">＋</button></form>
    <p id="count" class="muted">正在连接…</p>
    <div class="privacy"><span>↗ 在你的设备上思考</span><p>文档和问题的向量化在浏览器本地完成。原文与向量保存至本地 seekdb。</p></div>
    <div class="sidebar-foot">LOCAL INFERENCE / REAL RETRIEVAL<br>MiniLM · seekdb</div>
  </aside>
  <main><header><div><p class="eyebrow">A SMALL LIBRARY FOR BIG IDEAS</p><h1>让笔记，回答你的问题<span>。</span></h1><p class="intro">把零散的知识放在一起。用自然语言，找回值得记住的那一段。</p></div><span class="edition">KNOWLEDGE LAB<br>NO. 01 — LOCAL FIRST</span></header>
    <div class="runtime"><span class="dot"></span><strong id="backend">模型尚未加载</strong><span id="backend-detail">首次使用会从本地服务加载开源模型</span>
      <select id="runtime-mode" aria-label="推理模式"><option value="auto">WebGPU 优先 · 自动降级</option><option value="wasm-q8">WASM / INT8</option><option value="wasm-fp32">WASM / FP32 基线</option><option value="unavailable">诊断：模拟 WebGPU 不可用</option></select></div>
    <section class="workspace"><article class="import-card"><div class="card-heading"><span class="number">01</span><h2>放入你的资料</h2><span class="tag">IMPORT</span></div>
      <label for="document-title">标题</label><input id="document-title" placeholder="这份资料叫什么？" value="我的笔记">
      <label for="document-text">内容</label><textarea id="document-text" placeholder="粘贴笔记、文章或文档内容…\n\n长文会按段落切分，保留原文供检索回看。"></textarea>
      <label class="file-label" for="file">↥ 选择 TXT / Markdown 文件</label><input id="file" type="file" accept=".txt,.md,.markdown" multiple>
      <div class="actions"><button id="import" class="primary">加入知识库 ↗</button><button id="demo" class="quiet">导入示例资料</button></div>
      <progress id="progress" value="0" max="1"></progress><p id="status" role="status">就绪。可以先导入示例资料体验检索。</p>
    </article><article class="search-card"><div class="card-heading"><span class="number">02</span><h2>从一个问题开始</h2><span class="tag">DISCOVER</span></div>
      <form id="search-form"><label for="query">你想找什么？</label><div class="search-box"><input id="query" placeholder="例如：数据库如何在崩溃后恢复数据？" value="How can transactions survive a database crash?"><button class="primary" aria-label="搜索">↗</button></div></form>
      <p class="hint">语义理解 + 关键词匹配 · seekdb 数据库内混合检索</p><div id="timing"></div><div id="results"><div class="empty"><span>⌁</span><h3>你的答案，藏在笔记里</h3><p>导入资料后，试着问一个问题。</p></div></div>
    </article></section>
    <section class="experiment"><div><p class="eyebrow">MEASURE, THEN OPTIMIZE</p><h2>每一份提速，都有依据。</h2><p>在固定的 24 篇资料、12 个问题上比较模型精度、批处理和本地后端。保留全部原始记录。</p></div><button id="benchmark">运行量化对照实验 ↗</button><pre id="experiment-status">FP32 / INT8 / WebGPU FP16 · Recall@5 · nDCG@5</pre></section>
    <footer><span>✳ LOCAL NOTES</span><span>模型：all-MiniLM-L6-v2 · 384 维 · 无云端推理</span></footer>
  </main>`;

const $ = id => document.getElementById(id);
const worker = new Worker(new URL('./embedding.worker.js', import.meta.url), { type: 'module' });
let nextId = 0, busy = false, selected = localStorage.getItem('local-notes-collection') || 'notes';
let epoch = 0, fileDocs = [];
const requests = new Map();
const setStatus = text => { $('status').textContent = text; };
const settings = () => $('runtime-mode').value === 'unavailable' ? { backend: 'auto', simulateNoGPU: true } : $('runtime-mode').value === 'auto' ? { backend: 'auto' } :
  { backend: 'wasm', dtype: $('runtime-mode').value === 'wasm-fp32' ? 'fp32' : 'q8' };

function displayRuntime(runtime) {
  $('backend').textContent = `${runtime.backend.toUpperCase()} / ${runtime.dtype === 'q8' ? 'INT8' : runtime.dtype.toUpperCase()}`;
  $('backend-detail').textContent = runtime.reason || '浏览器 GPU 本地计算';
}
worker.onmessage = ({ data }) => {
  const request = requests.get(data.id);
  if (!request) return;
  if (data.type === 'progress') {
    if (data.event.status === 'progress') setStatus(`加载模型 ${data.event.file} · ${Math.round(data.event.progress || 0)}%`);
  } else if (data.type === 'inference-progress') {
    displayRuntime(data.runtime); $('progress').value = data.done / data.total;
    setStatus(`本地向量化 ${data.done} / ${data.total}`);
  } else {
    requests.delete(data.id);
    if (data.type === 'error') request.reject(new Error(data.error));
    else { displayRuntime(data.runtime); request.resolve(data); }
  }
};
worker.onerror = event => {
  for (const request of requests.values()) request.reject(new Error(event.message));
  requests.clear();
};
function embed(texts, config = settings(), batchSize = 4) {
  const id = ++nextId;
  return new Promise((resolve, reject) => { requests.set(id, { resolve, reject }); worker.postMessage({ id, texts, settings: config, batchSize }); });
}
async function api(url, body) {
  const response = await fetch(`/api/${url}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
async function lock(task) {
  if (busy) return;
  busy = true;
  for (const el of document.querySelectorAll('button, select, input, textarea')) el.disabled = true;
  try { await task(); } catch (error) { setStatus(`操作未完成：${error.message}`); $('experiment-status').textContent = `操作未完成：${error.message}`; }
  finally { busy = false; for (const el of document.querySelectorAll('button, select, input, textarea')) el.disabled = false; }
}
async function refreshCollections() {
  const collections = await api('collections');
  if (!collections.some(c => c.name === selected)) selected = 'notes';
  $('collection').replaceChildren(...collections.map(c => {
    const option = document.createElement('option'); option.value = c.name; option.textContent = `${c.name} · ${c.count} 段`; return option;
  }));
  $('collection').value = selected;
  localStorage.setItem('local-notes-collection', selected);
  $('count').textContent = `${collections.find(c => c.name === selected)?.count || 0} 段资料 · 本地 seekdb`;
}
$('collection').onchange = () => {
  selected = $('collection').value; ++epoch;
  localStorage.setItem('local-notes-collection', selected);
  $('results').replaceChildren(); $('timing').textContent = ''; refreshCollections().catch(error => setStatus(error.message));
};
$('create-form').onsubmit = event => { event.preventDefault(); lock(async () => {
  const name = $('new-name').value.trim(); await api('collections', { name }); selected = name; ++epoch;
  $('new-name').value = ''; $('results').replaceChildren(); await refreshCollections();
}); };
$('file').onchange = async () => {
  fileDocs = [];
  for (const file of $('file').files) {
    if (file.size > 1024 * 1024) { setStatus('单个文件最大 1 MB'); $('file').value = ''; fileDocs = []; return; }
    const text = await file.text(); fileDocs.push(...splitDocument(text).map(text => ({ title: file.name, text })));
  }
  setStatus(`已选择 ${fileDocs.length} 段文件资料`);
};
async function stableId(document) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${document.title}\n${document.text}`));
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
}
async function importDocs(documents, name = selected, config = settings(), batchSize = 4) {
  if (!documents.length) throw new Error('请先填写资料或选择文件');
  const unique = new Map();
  for (const d of documents) { const id = d.id || await stableId(d); unique.set(id, { ...d, id }); }
  documents = [...unique.values()];
  const encoded = await embed(documents.map(d => d.text), config, batchSize);
  let writeMs = 0, refreshMs = 0;
  for (let start = 0; start < documents.length; start += 64) {
    const result = await api(`collections/${encodeURIComponent(name)}/import`, { model: modelKey,
      documents: documents.slice(start, start + 64).map((d, i) => ({ ...d,
        vector: encoded.vectors[start + i], dtype: encoded.runtime.dtype })) });
    writeMs += result.writeMs; refreshMs += result.refreshMs;
  }
  return { runtime: encoded.runtime, embeddingMs: encoded.inferenceMs, writeMs, refreshMs, count: documents.length };
}
$('import').onclick = () => lock(async () => {
  const documents = [...fileDocs, ...splitDocument($('document-text').value).map(text => ({ title: $('document-title').value || '未命名笔记', text }))];
  const result = await importDocs(documents); await refreshCollections(); $('progress').value = 1;
  setStatus(`已写入并刷新 ${result.count} 段 · 向量化 ${result.embeddingMs.toFixed(0)} ms · 写入 ${result.writeMs.toFixed(0)} ms · 刷新 ${result.refreshMs.toFixed(0)} ms`);
});
const evaluation = () => fetch('/data/evaluation.json').then(r => r.json());
$('demo').onclick = () => lock(async () => {
  const result = await importDocs((await evaluation()).documents); await refreshCollections();
  setStatus(`24 篇示例资料已就绪 · 本地向量化 ${result.embeddingMs.toFixed(0)} ms`);
});
function renderResults(response) {
  $('results').replaceChildren(...response.results.map((r, i) => {
    const article = document.createElement('article'); article.className = 'result';
    const rank = document.createElement('span'); rank.className = 'rank'; rank.textContent = String(i + 1).padStart(2, '0');
    const content = document.createElement('div'), title = document.createElement('h3'), text = document.createElement('p'), meta = document.createElement('small');
    title.textContent = r.metadata?.title || r.id; text.textContent = r.text;
    meta.textContent = `${r.id.slice(0, 16)} · RRF ${Number(r.score).toFixed(4)}`;
    content.append(title, text, meta); article.append(rank, content); return article;
  }));
  if (!response.results.length) $('results').textContent = '当前知识库暂无匹配资料。';
}
$('search-form').onsubmit = event => { event.preventDefault(); lock(async () => {
  const text = $('query').value.trim(), name = selected, currentEpoch = epoch, start = performance.now();
  if (!text) throw new Error('请先输入问题');
  const encoded = await embed([text]);
  const response = await api(`collections/${name}/search`, { text, vector: encoded.vectors[0], k: 5, mode: 'hybrid' });
  if (currentEpoch !== epoch || selected !== name) return;
  renderResults(response);
  $('timing').textContent = `向量化 ${encoded.inferenceMs.toFixed(0)} ms · 数据库 ${response.databaseMs.toFixed(0)} ms · 总计 ${(performance.now() - start).toFixed(0)} ms`;
  setStatus('检索完成，结果由 seekdb 混合排序返回');
}); };

$('benchmark').onclick = () => lock(async () => {
  const dataset = await evaluation();
  const manifest = await fetch('/models/manifest.json').then(r => r.json());
  const report = { kind: 'browser-benchmark', timestamp: new Date().toISOString(), model: MODEL,
    userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemoryGiB: navigator.deviceMemory || null, crossOriginIsolated: self.crossOriginIsolated,
    corpusDocuments: dataset.documents.length, queries: dataset.queries.length, k: 5, manifest, runs: [] };
  const configs = [
    { label: 'fp32_wasm_batch1', settings: { backend: 'wasm', dtype: 'fp32' }, batchSize: 1 },
    { label: 'int8_wasm_batch1', settings: { backend: 'wasm', dtype: 'q8' }, batchSize: 1 },
    { label: 'int8_wasm_batch4', settings: { backend: 'wasm', dtype: 'q8' }, batchSize: 4 },
    { label: 'auto_gpu_fp16_batch4', settings: { backend: 'auto' }, batchSize: 4 },
  ];
  // Dedicated collections keep the user's notes intact and make each precision independent.
  const runId = Date.now().toString(36);
  for (const config of configs) {
    const name = `bench_${runId}_${report.runs.length}`;
    await api('collections', { name });
    const run = { ...config, collection: name, repetitions: [] };
    const cold = await embed(['warm up the embedding model'], config.settings, 1);
    run.initialLoad = cold.runtime; run.firstInferenceMs = cold.inferenceMs;
    for (let repeat = 0; repeat < 3; ++repeat) {
      $('experiment-status').textContent = `${config.label} · 第 ${repeat + 1}/3 轮 · 正在浏览器本地执行…`;
      const imported = await importDocs(dataset.documents, name, config.settings, config.batchSize);
      const queries = [];
      for (const q of dataset.queries) {
        const start = performance.now(), encoded = await embed([q.text], config.settings, 1);
        const response = await api(`collections/${name}/search`, { text: q.text, vector: encoded.vectors[0], k: 5, mode: 'hybrid' });
        const endToEndMs = performance.now() - start;
        const vectorResponse = await api(`collections/${name}/search`, { text: q.text, vector: encoded.vectors[0], k: 5, mode: 'vector' });
        const ids = response.results.map(r => r.id), vectorIds = vectorResponse.results.map(r => r.id);
        queries.push({ text: q.text, relevant: q.relevant, ids, vectorIds, hybrid: retrievalMetrics(ids, q.relevant),
          vector: retrievalMetrics(vectorIds, q.relevant), queryEmbeddingMs: encoded.inferenceMs,
          databaseMs: response.databaseMs, vectorDatabaseMs: vectorResponse.databaseMs, endToEndMs });
      }
      run.repetitions.push({ ...imported, queries });
    }
    const allQueries = run.repetitions.flatMap(r => r.queries);
    const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
    run.summary = { runtime: run.repetitions[0].runtime,
      documentEmbeddingMedianMs: median(run.repetitions.map(r => r.embeddingMs)),
      writeMedianMs: median(run.repetitions.map(r => r.writeMs)), refreshMedianMs: median(run.repetitions.map(r => r.refreshMs)),
      queryEmbeddingMedianMs: median(allQueries.map(q => q.queryEmbeddingMs)),
      databaseMedianMs: median(allQueries.map(q => q.databaseMs)), endToEndMedianMs: median(allQueries.map(q => q.endToEndMs)),
      recallAt5: mean(allQueries.map(q => q.hybrid.recall)), ndcgAt5: mean(allQueries.map(q => q.hybrid.ndcg)),
      vectorRecallAt5: mean(allQueries.map(q => q.vector.recall)), vectorNdcgAt5: mean(allQueries.map(q => q.vector.ndcg)) };
    report.runs.push(run);
  }
  const saved = await api('measurements', report);
  $('experiment-status').textContent = report.runs.map(r => {
    const s = r.summary;
    return `${r.label} → ${s.runtime.backend}/${s.runtime.dtype}\n文档 ${s.documentEmbeddingMedianMs.toFixed(1)} ms · 查询总延迟 ${s.endToEndMedianMs.toFixed(1)} ms · Recall@5 ${s.recallAt5.toFixed(3)} · nDCG@5 ${s.ndcgAt5.toFixed(3)}`;
  }).join('\n\n') + `\n\n原始数据已保存：${saved.saved}`;
  await refreshCollections(); setStatus('实验完成；全部原始记录已保存');
});
refreshCollections().catch(error => setStatus(`连接失败：${error.message}`));
