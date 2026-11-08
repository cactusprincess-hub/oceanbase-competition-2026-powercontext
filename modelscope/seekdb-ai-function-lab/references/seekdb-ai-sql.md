# seekdb AI SQL Probes

These probes target the default mock server at `127.0.0.1:8000`. Run only the section relevant to the behavior under test.

## Register Models and Endpoints

```sql
CALL DBMS_AI_SERVICE.CREATE_AI_MODEL(
  'lab_complete', '{"type":"completion","model_name":"lab-chat"}');
CALL DBMS_AI_SERVICE.CREATE_AI_MODEL_ENDPOINT(
  'lab_complete_ep',
  '{"ai_model_name":"lab_complete","scope":"all","url":"http://127.0.0.1:8000/v1/chat/completions","access_key":"local-only","provider":"openai"}');

CALL DBMS_AI_SERVICE.CREATE_AI_MODEL(
  'lab_embed', '{"type":"dense_embedding","model_name":"lab-embed"}');
CALL DBMS_AI_SERVICE.CREATE_AI_MODEL_ENDPOINT(
  'lab_embed_ep',
  '{"ai_model_name":"lab_embed","scope":"all","url":"http://127.0.0.1:8000/v1/embeddings","access_key":"local-only","provider":"openai"}');

CALL DBMS_AI_SERVICE.CREATE_AI_MODEL(
  'lab_rerank', '{"type":"rerank","model_name":"lab-rerank"}');
CALL DBMS_AI_SERVICE.CREATE_AI_MODEL_ENDPOINT(
  'lab_rerank_ep',
  '{"ai_model_name":"lab_rerank","scope":"all","url":"http://127.0.0.1:8000/v1/rerank","access_key":"local-only","provider":"openai"}');
```

Use unique names when the database already contains these models. Inspect `oceanbase.DBA_OB_AI_MODELS` and `oceanbase.DBA_OB_AI_MODEL_ENDPOINTS` before replacing metadata.

## Focused Probes

```sql
SELECT AI_PROMPT('{0}/{0}/{1}', JSON_ARRAY(1, TRUE), JSON_OBJECT('k', 2));
SELECT AI_COMPLETE('lab_complete', 'hello');
SELECT JSON_LENGTH(AI_EMBED('lab_embed', 'single row')) AS dimension;
SELECT JSON_LENGTH(AI_RERANK(
  'lab_rerank', 'apple', '["apple pie","banana","apple tree"]', NULL
)) AS result_count;
```

Batch and NULL-position probe:

```sql
CREATE TABLE ai_lab_batch(id INT PRIMARY KEY, content TEXT);
INSERT INTO ai_lab_batch VALUES (1, 'first'), (2, NULL), (3, 'third');
SELECT id, JSON_LENGTH(AI_EMBED('lab_embed', content)) AS dimension
FROM ai_lab_batch ORDER BY id;
DROP TABLE ai_lab_batch;
```

## Ollama-Native Routes

Register completion with `provider: "ollama"` and URL `http://127.0.0.1:8000/api/generate`. Register embeddings with the same provider and URL `http://127.0.0.1:8000/api/embed`. Empty access keys are expected for a local Ollama endpoint.

## Cleanup

Drop models before endpoints only if the target seekdb build permits it; otherwise reverse the order indicated by its dependency error. Never use broad metadata deletion.

```sql
CALL DBMS_AI_SERVICE.DROP_AI_MODEL('lab_complete');
CALL DBMS_AI_SERVICE.DROP_AI_MODEL_ENDPOINT('lab_complete_ep');
CALL DBMS_AI_SERVICE.DROP_AI_MODEL('lab_embed');
CALL DBMS_AI_SERVICE.DROP_AI_MODEL_ENDPOINT('lab_embed_ep');
CALL DBMS_AI_SERVICE.DROP_AI_MODEL('lab_rerank');
CALL DBMS_AI_SERVICE.DROP_AI_MODEL_ENDPOINT('lab_rerank_ep');
```
