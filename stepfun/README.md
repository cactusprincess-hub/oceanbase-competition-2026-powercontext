# Local Notes：浏览器本地推理知识库

OceanBase 2026 初赛 StepFun 生态题。文档和问题都在浏览器 Web Worker 中生成 embedding；本地 Node 服务通过 **seekdb-js** 保存原文、元数据和向量，并用**一条参数化 SQL**在 seekdb 中完成全文、向量候选排序及 RRF 融合。服务端不包含推理代码或推理 API。

## 启动

需要 Node.js **22.9+**、npm、curl 和当前 Chrome/Edge。浏览器页面需使用 `http://127.0.0.1` 或 `http://localhost`，以允许本地安全上下文中的 WebGPU。首次准备模型需要连接 Hugging Face，准备完毕后模型推理和检索均可离线运行。

```bash
cd stepfun
npm ci
cp .env.example .env
npm run models
```

模型下载固定公开 revision，获取 FP32、INT8、FP16 三种 ONNX 权重及相同的 tokenizer/config；记录每个文件的实际大小、SHA-256 和下载时间。无需 Hugging Face token。默认 embedding peer 被有意禁用：seekdb-js 自动安装的服务端 embedding 包含 `workspace:*` peer，且本项目明确由浏览器提供全部向量；`.npmrc` 已处理这一点。

选择一种 seekdb 启动方式：

```bash
# 方式 A：Docker Compose。镜像锁定 1.4.0 和多架构 digest。
bash scripts/start-db.sh

# 方式 B：已有 seekdb 1.4+ 本地二进制。
# 保持该终端运行，另开终端启动应用。
SEEKDB_BIN=/absolute/path/to/seekdb bash scripts/start-db.sh
```

二进制方式的数据默认保存在 `${XDG_DATA_HOME:-$HOME/.local/share}/local-notes-seekdb`，可用 `SEEKDB_BASE_DIR` 指定另一个持久化目录；使用较短的路径可避免 macOS Unix socket 路径长度限制。脚本使用至少 2 GiB 的日志盘配置。

已有本地 seekdb 时无需重复启动，将 `.env` 的 host、port、user、password 改为其连接信息即可。应用在独立数据库 `local_knowledge_lab` 中工作。Docker 使用持久化卷挂载 `/var/lib/seekdb`，数据库端口仅绑定本机。Docker 环境配置在本机未实际启动；本次真实数据库验证使用自行构建的 seekdb 1.4.0 服务器。

等待 seekdb 可连接后：

```bash
npm run build
npm start
# 打开 http://127.0.0.1:5173
```

开发模式为 `npm run dev`。本机测试时数据库端口为 `12016`，应用端口为 `5174`：`PORT=5174 SEEKDB_PORT=12016 npm start`。这些仅是测试配置，复现默认使用 `.env` 中的端口。

## 操作与验收

1. 点击“导入示例资料”，等待本地向量化、写入与索引刷新。界面显示当前实际后端和精度。
2. 搜索 `How can transactions survive a database crash?`，结果显示原文、文档 ID 和数据库 RRF 分数。亦可粘贴文本或选择 TXT/Markdown 文件导入；长文按段落切分为不超过 650 个 Unicode 字符的片段。
3. 连续导入同一资料，内容哈希 ID 保持稳定，upsert 不重复。加入新资料后等待刷新完成，再检索新增内容。
4. 左侧创建另一个英文名知识库并切换；各集合完全隔离。当前集合保存在 localStorage。操作中禁止切换，另用 epoch 检查丢弃过期检索响应。页面刷新和应用服务重启后数据仍从 seekdb 读取。
5. 推理模式默认 WebGPU 优先，要求可用 adapter 和 `shader-f16`；模型初始化失败也自动回退 WASM/INT8。可显式选 WASM。诊断选项“模拟 WebGPU 不可用”将 adapter 结果置空以验证自动降级分支，界面明确标注模拟；这是故障注入，不宣称测试了另一台无 GPU 设备。
6. 点击“运行量化对照实验”：4 组配置 × 3 轮 × 12 个查询；另查精确向量排名以检查量化质量，避免全文掩盖语义质量变化。原始文档、查询、标签见 `data/evaluation.json`；所有结果和分阶段耗时写到 `results/browser-*.json`，并在界面显示摘要。每次实验新建独立集合，保留用户笔记。

模型下载与服务端文件静态分发属于权重获取，实际推理在浏览器。公开模型下载可能较慢，可在网络畅通的机器运行 `npm run models` 后复制整个 `models/` 目录。客户端不请求任何远程推理服务。

## 数据层与正确性

集合由 seekdb-js `Schema` 创建：384 维、cosine、HNSW 向量索引；`document` 使用 IK smart 全文索引。记录包含 `_id`、`document`、`embedding`、`metadata`（标题、模型 revision、精度）。所有集合使用同一固定模型、mean pooling 和 L2 normalization，服务端验证 384 个有限浮点数及归一化长度。客户端不能提供任意 SQL 或表名。

混合查询的全文候选按 BM25 取前 50 个，向量候选按精确 cosine distance 取前 50 个；seekdb 用 `ROW_NUMBER`、`UNION ALL`、`GROUP BY` 计算 `Σ 1/(60+rank)`，按融合分数和 ID 排序。整个过程是一条 SQL，浏览器与 Node 均不获取两路候选后合并。精确向量扫描适合迷你知识库；不宣称大规模近似检索性能。

采用固定参数化 SQL的原因：seekdb-js 1.4 的 `hybridSearch()` 动态 SQL 校验会误拒问题中的 `call` / `load` / `update` 等词。这里仍通过 seekdb-js `execute()` 完成数据库侧混合检索，保留参数绑定。写入每批最多 64 条，一批后调用 `refresh_index()`；串行执行复合数据库操作，避免共享连接上的状态竞争。失败不报成功，已完成的小批次可凭相同 ID 安全重试。

## 检查

```bash
npm test
# 运行实际 seekdb 验收：增量写入、重复导入、集合隔离、刷新、重新连接回读。
SEEKDB_TEST=1 npm test
```

`SEEKDB_TEST=1` 的测试只删除自己本次创建的随机测试集合。常规测试覆盖检索指标的数学定义、Unicode 切分和非法向量拒绝。真实数据库测试同时覆盖含 SQL 关键字、引号和分号的自然语言问题。

实测数据、条件和局限见 [一页技术说明](TECHNICAL_NOTE.md) 与 `results/`。本地通过不代表平台已得 20 分。
