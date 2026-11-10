# 本地验收记录 · 2026-11-10

- 构建：`npm ci` 对应的 npm lock 已提交；本次实际安装使用 `npm install`，生产构建 `npm run build` 通过。
- 真实数据库：在已有 seekdb 1.4.0 端口 12016 上，`SEEKDB_PORT=12016 SEEKDB_TEST=1 npm test` 为 **4/4 通过**。
- 从空目录启动：`SEEKDB_BASE_DIR=/tmp/local-notes-native-check SEEKDB_BIN=<本机编译的 seekdb> SEEKDB_PORT=12018 bash scripts/start-db.sh`，首次引导成功；同样的真实数据库测试 **4/4 通过**。
- 实际数据库进程重启：写入集合 `restart_check` 的 `restart` 文档；停止服务器、用同一数据目录重启，再通过 seekdb-js 的混合 SQL 回读，返回同一 ID、原文和模型元数据。结果标记 `REAL_DATABASE_RESTART_PASS`。
- 生产页面：`http://127.0.0.1:5174` 实际浏览器执行 WebGPU/FP16 示例导入、查询；4 组 × 3 轮量化实验的完整记录为 `browser-1794306350102-32b0c353.json`。
- 降级路径：UI 选择明确标注的“模拟 WebGPU 不可用”，经过正常自动选后端分支转到真实 WASM/INT8；新建 `ui_check_1110`，导入新文本，刷新索引后查得 `violet lighthouse` 原文。重复导入后仍为 **1 条**。截图 `wasm-fallback.jpg`。
- 这项诊断模拟的是 adapter 缺失，不是另一台实际无 GPU 设备；WASM 和 WebGPU 的推理本身均实际执行。Docker 在本机未运行，不冒充 Docker 验证结果。
- 启动脚本语法：`bash -n scripts/start-db.sh` 通过；`git diff --check` 通过。

初始脚本曾使用过小的日志盘导致新实例引导失败；最终脚本已改为 seekdb 要求的至少 **2 GiB**，并通过上述空目录启动验证。实验计时早于这一启动脚本检查，使用一直正常工作的端口 12016 实例；未修改实验数据。
