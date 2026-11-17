import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { Store, Conflict, initialState } from '../server/store.mjs';
import { createApp } from '../server/app.mjs';

// Real seekdb is required: this suite deliberately has no in-memory database substitute.
test('seekdb transaction, stale-write protection, independent sessions and reconnect', async () => {
  const store = new Store();
  const session = randomBytes(16).toString('hex');
  const other = randomBytes(16).toString('hex');
  try {
    await store.init();
    const original = await store.context(session);
    assert.equal(original.scenes.length, 4);
    assert.equal(original.version, 0);
    const payload = {session_id: session, version: 0, state: {...initialState(), room: '站台', turns: 1}, command: '去站台', reply: '站台'};
    const results = await Promise.allSettled([store.commit(payload), store.commit(payload)]);
    assert.equal(results.filter(r => r.status === 'fulfilled').length, 1);
    assert.ok(results.find(r => r.status === 'rejected').reason instanceof Conflict);
    const [[history]] = await store.pool.execute('SELECT COUNT(*) AS count FROM midnight_game.history WHERE session_id=?', [session]);
    assert.equal(Number(history.count), 1);
    assert.equal((await store.context(other)).version, 0);
    const reconnected = new Store();
    try {
      const saved = await reconnected.context(session);
      assert.equal(saved.state.room, '站台');
      assert.equal(saved.version, 1);
    } finally { await reconnected.close(); }
  } finally { await store.close(); }
});

test('HTTP rejects unauthenticated and invalid progress without changing stored state', async () => {
  const store = new Store();
  const token = randomBytes(24).toString('hex');
  await store.init();
  const server = createApp(store, token).listen(0, '127.0.0.1');
  await new Promise(resolve => server.once('listening', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const session_id = randomBytes(16).toString('hex');
  const post = (route, body, authorized = true) => fetch(base + route, {method: 'POST', headers: {'Content-Type': 'application/json', ...(authorized ? {'Authorization': `Bearer ${token}`} : {})}, body: JSON.stringify(body)});
  try {
    assert.equal((await post('/context', {session_id}, false)).status, 401);
    assert.equal((await post('/context', {session_id: 'not-a-session'})).status, 400);
    assert.equal((await post('/context', {session_id})).status, 200);
    assert.equal((await post('/commit', {session_id, version: 0, command: 'bad', reply: '', state: {...initialState(), turns: -1}})).status, 400);
    assert.equal((await store.context(session_id)).version, 0);
  } finally { await new Promise(resolve => server.close(resolve)); await store.close(); }
});
