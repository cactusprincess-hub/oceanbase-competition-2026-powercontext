import { SeekdbAdminClient, SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';
import { performance } from 'node:perf_hooks';
import { MODEL, modelKey, validateVector } from '../shared/model.mjs';

const prefix = 'local_lab_';
export function validateName(name) {
  if (typeof name !== 'string' || !/^[a-z][a-z0-9_]{0,39}$/.test(name)) {
    throw new Error('知识库名称需为 1–40 位小写字母、数字或下划线，并以字母开头');
  }
  return prefix + name;
}

export async function openStore() {
  const config = { host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '' };
  const database = process.env.SEEKDB_DATABASE || 'local_knowledge_lab';
  const admin = new SeekdbAdminClient(config);
  try {
    const existing = await admin.listDatabases();
    if (!existing.some(db => db.name === database)) await admin.createDatabase(database);
  } finally { await admin.close(); }
  const client = new SeekdbClient({ ...config, database });
  // Serialize compound writes/refreshes and session operations on this connection.
  let pending = Promise.resolve();
  const serial = fn => {
    const result = pending.then(fn);
    pending = result.catch(() => {});
    return result;
  };
  const collection = name => client.getCollection({ name: validateName(name), embeddingFunction: null });
  const create = name => serial(async () => {
    const fullName = validateName(name);
    if (!await client.hasCollection(fullName)) {
      await client.createCollection({ name: fullName,
        schema: new Schema({ fulltextIndex: new FulltextIndexConfig('ik', { ik_mode: 'smart' }),
          vectorIndex: new VectorIndexConfig({ hnsw: { dimension: MODEL.dimension, distance: 'cosine' },
            embeddingFunction: null }) }) });
    }
    return { name, count: await (await collection(name)).count() };
  });
  return {
    create,
    list: () => serial(async () => {
      const all = await client.listCollections({ withEmbeddingFunction: false });
      return Promise.all(all.filter(c => c.name.startsWith(prefix)).map(async c =>
        ({ name: c.name.slice(prefix.length), count: Number(await c.count()) })));
    }),
    import: (name, documents, suppliedModel) => {
      validateName(name);
      if (suppliedModel !== modelKey) throw new Error('模型版本不匹配');
      if (!Array.isArray(documents) || !documents.length || documents.length > 64) {
        throw new Error('每次写入需包含 1–64 个文档片段');
      }
      const ids = new Set();
      for (const d of documents) {
        if (typeof d.id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(d.id) || ids.has(d.id) ||
            typeof d.text !== 'string' || !d.text.trim() || d.text.length > 12000 ||
            typeof d.title !== 'string' || d.title.length > 512) throw new Error('文档格式无效或 ID 重复');
        if (!['q8', 'fp16', 'fp32'].includes(d.dtype)) throw new Error('未知模型精度');
        validateVector(d.vector);
        ids.add(d.id);
      }
      return serial(async () => {
        const c = await collection(name);
        const start = performance.now();
        await c.upsert({ ids: documents.map(d => d.id), documents: documents.map(d => d.text),
          embeddings: documents.map(d => d.vector),
          metadatas: documents.map(d => ({ title: d.title, model: modelKey, dtype: d.dtype })) });
        const writeMs = performance.now() - start;
        const refreshStart = performance.now();
        await c.refresh_index();
        return { written: documents.length, count: Number(await c.count()), writeMs,
          refreshMs: performance.now() - refreshStart };
      });
    },
    search: (name, text, vector, k = 5, mode = 'hybrid') => {
      validateName(name);
      validateVector(vector);
      if (typeof text !== 'string' || !text.trim() || text.length > 2000) throw new Error('请输入不超过 2000 字符的查询');
      if (!Number.isInteger(k) || k < 1 || k > 20 || !['hybrid', 'vector'].includes(mode)) throw new Error('检索参数无效');
      return serial(async () => {
        const c = await collection(name);
        const start = performance.now();
        const include = ['documents', 'metadatas', 'distances'];
        if (mode === 'hybrid') {
          // Fixed, parameterized SQL avoids SDK 1.4's dynamic-SQL keyword check,
          // which incorrectly rejects ordinary search text such as "model load".
          // Both candidate rankings and RRF are computed inside one DB statement.
          if (!/^[a-f0-9]{32}$/.test(c.collectionId)) throw new Error('无效的集合标识');
          const table = '`c$v2$' + c.collectionId + '`';
          const sql = `SELECT * FROM (WITH lexical_top AS (
            SELECT _id, MATCH(document) AGAINST(? IN NATURAL LANGUAGE MODE) AS relevance
            FROM ${table} WHERE MATCH(document) AGAINST(? IN NATURAL LANGUAGE MODE) > 0
            ORDER BY relevance DESC, _id LIMIT 50
          ), semantic_top AS (
            SELECT _id, COSINE_DISTANCE(embedding, ?) AS distance FROM ${table}
            ORDER BY distance, _id LIMIT 50
          ), candidates AS (
            SELECT _id, 1.0 / (60 + ROW_NUMBER() OVER (ORDER BY relevance DESC, _id)) AS score FROM lexical_top
            UNION ALL
            SELECT _id, 1.0 / (60 + ROW_NUMBER() OVER (ORDER BY distance, _id)) AS score FROM semantic_top
          ), fused AS (
            SELECT _id, SUM(score) AS score FROM candidates GROUP BY _id
          ) SELECT d._id AS id, d.document AS text, d.metadata AS metadata, f.score
            FROM fused f JOIN ${table} d ON d._id = f._id
            ORDER BY f.score DESC, d._id LIMIT ?) AS hybrid_results ORDER BY score DESC, id`;
          const rows = await client.execute(sql, [text, text, JSON.stringify(vector), k]);
          return { collection: name, databaseMs: performance.now() - start,
            engine: 'seekdb SQL / full-text + cosine + RRF', results: rows.map(row => ({
              id: row.id.toString(), text: row.text?.toString(), score: Number(row.score),
              metadata: typeof row.metadata === 'string' || Buffer.isBuffer(row.metadata)
                ? JSON.parse(row.metadata.toString()) : row.metadata })) };
        }
        const result = await c.query({ queryEmbeddings: [vector], nResults: k, approximate: false, include });
        return { collection: name, databaseMs: performance.now() - start,
          engine: 'seekdb exact cosine',
          results: (result.ids[0] || []).map((id, i) => ({ id,
            text: result.documents?.[0]?.[i], metadata: result.metadatas?.[0]?.[i],
            score: result.distances?.[0]?.[i] })) };
      });
    },
    close: () => serial(() => client.close()),
  };
}
