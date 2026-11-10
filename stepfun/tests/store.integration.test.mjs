import test from 'node:test';
import assert from 'node:assert/strict';
import { SeekdbClient } from 'seekdb';
import { openStore } from '../server/store.mjs';
import { modelKey } from '../shared/model.mjs';

test('real seekdb: incremental upserts, refreshed hybrid retrieval, isolation and restart', {
  skip: process.env.SEEKDB_TEST !== '1',
}, async () => {
  const token = Date.now().toString(36);
  const first = `test_a_${token}`, second = `test_b_${token}`;
  let store = await openStore();
  const vector = Array(384).fill(0); vector[0] = 1;
  const doc = (id, text) => ({ id, text, title: id, vector, dtype: 'q8' });
  try {
    await store.create(first); await store.create(second);
    await store.import(first, [doc('alpha', 'alpha durable transaction log')], modelKey);
    await store.import(first, [doc('beta', 'beta graph vector search')], modelKey);
    const repeated = await store.import(first, [doc('alpha', 'alpha updated durable log')], modelKey);
    assert.equal(repeated.count, 2);
    await store.import(second, [doc('other', 'alpha separate collection')], modelKey);
    const results = await Promise.all([
      store.search(first, 'alpha', vector, 5), store.search(second, 'alpha', vector, 5),
    ]);
    assert.deepEqual(new Set(results[0].results.map(r => r.id)), new Set(['alpha', 'beta']));
    assert.deepEqual(results[1].results.map(r => r.id), ['other']);
    assert.match(results[0].results.find(r => r.id === 'alpha').text, /updated/);
    const naturalLanguage = await store.search(first, "How can I update or load a model's data; is it safe?", vector, 5);
    assert.equal(naturalLanguage.results.length, 2);
    await store.close(); store = await openStore();
    assert.deepEqual((await store.search(second, 'alpha', vector, 5)).results.map(r => r.id), ['other']);
    assert.equal((await store.list()).find(c => c.name === first).count, 2);
  } finally {
    await store.close();
    const client = new SeekdbClient({ host: process.env.SEEKDB_HOST || '127.0.0.1',
      port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root',
      password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'local_knowledge_lab' });
    try { for (const name of [first, second]) if (await client.hasCollection(`local_lab_${name}`)) await client.deleteCollection(`local_lab_${name}`); }
    finally { await client.close(); }
  }
});
