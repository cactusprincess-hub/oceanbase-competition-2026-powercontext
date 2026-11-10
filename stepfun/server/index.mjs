import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { openStore } from './store.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const port = Number(process.env.PORT || 5173);
const store = await openStore();
await store.create('notes');
const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  const origin = req.get('origin');
  if (origin && origin !== `http://${req.get('host')}` && origin !== `https://${req.get('host')}`) {
    return res.status(403).json({ error: '仅允许同源访问本地服务' });
  }
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  next();
});
app.use(express.json({ limit: '8mb' }));
const endpoint = fn => async (req, res, next) => {
  try { res.json(await fn(req)); } catch (error) { next(error); }
};
app.get('/api/health', endpoint(() => ({ ok: true, database: 'seekdb', inference: 'browser-only' })));
app.get('/api/collections', endpoint(() => store.list()));
app.post('/api/collections', endpoint(req => store.create(req.body.name)));
app.post('/api/collections/:name/import', endpoint(req =>
  store.import(req.params.name, req.body.documents, req.body.model)));
app.post('/api/collections/:name/search', endpoint(req =>
  store.search(req.params.name, req.body.text, req.body.vector, req.body.k, req.body.mode)));
app.post('/api/measurements', endpoint(async req => {
  if (req.body?.kind !== 'browser-benchmark' || !Array.isArray(req.body.runs)) throw new Error('实验记录格式无效');
  const filename = `browser-${Date.now()}-${randomUUID().slice(0, 8)}.json`;
  await mkdir(path.join(root, 'results'), { recursive: true });
  await writeFile(path.join(root, 'results', filename), JSON.stringify(req.body, null, 2) + '\n', { flag: 'wx' });
  return { saved: `results/${filename}` };
}));
app.use('/models', express.static(path.join(root, 'models'), { maxAge: '1y', immutable: true }));
app.use('/runtime', express.static(path.join(root, 'node_modules/onnxruntime-web/dist'), { maxAge: '1y' }));
app.use('/data', express.static(path.join(root, 'data')));
if (process.env.NODE_ENV === 'production') {
  app.use(express.static(path.join(root, 'dist')));
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
}
app.use((error, req, res, next) => {
  console.error(error.message);
  res.status(400).json({ error: error.message });
});
const server = app.listen(port, '127.0.0.1', () => console.log(`Local Knowledge Lab: http://127.0.0.1:${port}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, async () => {
  server.close();
  await store.close();
  process.exit(0);
});
