export const MODEL = Object.freeze({
  id: 'Xenova/all-MiniLM-L6-v2',
  revision: '751bff37182d3f1213fa05d7196b954e230abad9',
  dimension: 384,
  pooling: 'mean',
  normalize: true,
  maxLength: 256,
});

export const modelKey = `${MODEL.id}@${MODEL.revision}`;

export function validateVector(vector) {
  if (!Array.isArray(vector) || vector.length !== MODEL.dimension ||
      vector.some(v => typeof v !== 'number' || !Number.isFinite(v))) {
    throw new Error(`向量必须包含 ${MODEL.dimension} 个有限数值`);
  }
  const norm = vector.reduce((sum, v) => sum + v * v, 0);
  if (Math.abs(norm - 1) > 0.02) throw new Error('需要归一化的向量');
  return vector;
}
