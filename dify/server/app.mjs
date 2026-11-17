import express from 'express';
import { timingSafeEqual } from 'node:crypto';
import { Conflict } from './store.mjs';

export function createApp(store, token) {
  if (!token || token.length < 24) throw new Error('GAME_TOKEN must contain at least 24 characters');
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({limit: '16kb'}));
  app.get('/health', async (_req, res) => { await store.pool.query('SELECT 1'); res.json({ok: true}); });
  app.use((req, res, next) => {
    const provided = Buffer.from(req.get('authorization') || '');
    const expected = Buffer.from(`Bearer ${token}`);
    if (provided.length !== expected.length || !timingSafeEqual(provided, expected)) return res.status(401).json({error: 'Unauthorized'});
    if (!/^[a-f0-9]{32}$/.test(req.body?.session_id || '')) return res.status(400).json({error: 'session_id must be 32 lowercase hex characters'});
    next();
  });
  app.post('/context', async (req, res) => res.json(await store.context(req.body.session_id)));
  app.post('/commit', async (req, res) => {
    const {state: s, version, command, reply} = req.body;
    if (!Number.isInteger(version) || version < 0 || typeof command !== 'string' || command.length > 100 ||
        typeof reply !== 'string' || reply.length > 6000 || !s ||
        !['大厅', '站台', '仓库', '广播室'].includes(s.room) ||
        !['playing', 'won', 'lost'].includes(s.status) ||
        !Number.isInteger(s.turns) || s.turns < 0 || s.turns > 12 ||
        !Number.isInteger(s.hints) || s.hints < 0 || s.hints > 12 ||
        !Array.isArray(s.inventory) || s.inventory.length > 4 || new Set(s.inventory).size !== s.inventory.length ||
        s.inventory.some(id => !['note', 'clock', 'ticket', 'rule'].includes(id))) return res.status(400).json({error: 'Invalid progress'});
    res.json(await store.commit(req.body));
  });
  app.use((err, _req, res, _next) => {
    if (err instanceof Conflict) return res.status(409).json({saved: false, error: err.message});
    if (err.type === 'entity.parse.failed' || err.type === 'entity.too.large') return res.status(400).json({error: 'Invalid JSON body'});
    console.error(err.message);
    res.status(500).json({saved: false, error: '数据库暂时不可用，请稍后重试。'});
  });
  return app;
}
