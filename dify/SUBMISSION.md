# 比赛提测材料

- 方向：AI生态 / Dify。
- 项目：午夜失物招领。
- 仓库：https://github.com/cactusprincess-hub/oceanbase-competition-2026-powercontext
- 分支：`2026-competition-ecosystem`。
- 作品目录：`dify/`。
- 启动及完整游玩说明：[README.md](README.md)。
- 自动配置、导入、发布并启动：在 `dify/` 执行 `bash start.sh`；完整验收执行 `bash start.sh --acceptance`。
- 可导入DSL：[midnight.yml](midnight.yml)，及[真实Dify导出DSL](results/dify-export.yml)。
- 源码：`workflow/` 的Code节点承担游戏核心规则，`server/`提供真实seekdb事务存储，`web/`和`scripts/web.py`提供网页体验。
- 初始化数据：[scenes.json](scenes.json)，首次启动自动入库。
- 固定依赖与部署：[compose.yaml](compose.yaml)、[package-lock.json](package-lock.json)。无需LLM或付费模型Key。
- 实际运行证据：[results/README.md](results/README.md)，含37次真实Dify调用、运行ID、数据快照、节点状态、桌面/手机截图与重启验证。
- CI：[44006878568](https://github.com/cactusprincess-hub/oceanbase-competition-2026-powercontext/actions/runs/44006878568)，实际验证源码SHA `f087d8e723378a730886ff3480878a8d4c6f0222`。

在比赛平台选择AI生态的Dify入口，填写上述仓库和分支，Commit ID使用包含本目录完整运行证据的提交完整SHA。若有作品目录栏填`dify`；若有说明链接栏填该提交下 `dify/README.md`；应用体验方式为依README本地启动。没有长期公网演示地址，不填写虚构网址。
