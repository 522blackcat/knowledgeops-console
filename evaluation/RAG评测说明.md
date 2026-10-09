# RAG 评测说明

这份文档回答三件事：**用什么测**、**怎么跑**、**目前实测到哪一步**。
所有数字都是本机真实跑出来的，不是估算；未跑完的会明确标"待补"。

相关代码：

| 文件 | 作用 |
| --- | --- |
| `evaluation/rag_metrics.py` | 离线指标 harness：直接 import 生产函数，算 hit@k / recall@k / MRR / empty |
| `evaluation/rag_eval_runner.py` | 在线断言 harness：打 `/api/rag/eval`，判每条用例过不过 |
| `evaluation/rag_cases_sampler.py` | 从上传文档本身抽用例（带 `anchor_text`） |
| `evaluation/rag_eval_cases_v3.json` | 当前默认用例集，160 条 |
| `evaluation/rag_eval_cases.json` | 旧 50 条手写题，已被 v3 取代，保留仅供对照 |

---

## 1. 为什么要两套 harness

两者测的不是同一件事，缺一个就会瞎：

- **runner（在线）** 回答"接口挂没挂、这条查询有没有返回结果"。它的产出是 `pass_rate`，
  本质是断言集合，**不能回答"这次改动让检索变好还是变坏"**。
- **metrics（离线）** 回答"前 k 条里有没有相关块、相关块找回来多少、第一条相关的排第几"。
  它需要原始语料文件，不经过 API，直接复用 `rag.bm25.score_rows`、
  `rag.retrieval.reciprocal_rank_fusion`、`rag.chunking.split_sections`，
  所以指标口径和生产代码不会各走各路。

生产回归防线是 metrics 那一侧；runner 是冒烟。

---

## 2. 用例集：v2 → v3

v2 是手写的 50 道 Python 面试题，和实际上传的文档对不上，已废弃为默认。
v3 从真实语料抽：

- 语料：`梁向东_项目经历专项面试_v9_FAST_RAG模拟面试.docx`、
  `梁向东_专业技能定向面试准备_v8_纯面试题版.docx`
- 规则：识别以 ？/? 结尾的问句行，跳过表格行（含 2 个以上 `|`）、超长行（>120 字符）、
  正文短于 6 字的行；跨文件按去空白后的文本查重（实测重 0 条）
- 抽样：每个文件各 80 条，`seed=20261007`，共 160 条
- 字段：`query`（去掉题号）+ `anchor_text`（原文那一行，逐字）+ `min_hits: 1`

重新抽样：

```bash
python evaluation/rag_cases_sampler.py --input-dir <语料目录> --seed 20261007
```

**runner 已改成用 `anchor_text` 判案**：锚点行必须出现在召回结果里。
不改的话，v3 用例没有任何期望词，`judge_case` 会退化成"只要返回了东西就算过"，
测试假绿。实测四组断言（锚命中→过、只返回无关内容→不过、空召回→不过、v2 用例行为不变）
全部通过。

---

## 3. 相关性标签口径（以及一个已知缺陷）

`rag_metrics.label_cases` 按"锚点优先"派生标签：

1. 包含 `anchor_text` 全文的块 = 相关块；
2. 找不到时退回锚点前 24 字符；
3. 还找不到就标 `anchor_missing`，该用例**不进指标平均**，
   而是通过 `unlabeled_cases` 数量暴露出来——块被切断导致锚点消失是真实缺陷，不做静默兜底。

因为锚点与分块无关，改 `chunk_size` 或改分块规则后同一套标签仍然可比，这是 A/B 能成立的前提。

**已知缺陷（下次改分块策略前必须先修）**：锚点是**问题行**，不是**答案段**。
如果某条用例的答案在下一段而问题行被切到别的块，那条会被算成 miss。
基线里 `hit@3=0` 的 3 条就是这种：

- `FAST Agent 怎么测试？`
- `RAG Evaluation 怎么设计？`
- `多项目 RAG 如何做数据隔离？`

标签应改成"问题行 → 下一问题行"之间的答案区间。
不修的话，"按题目分块"这类策略会**因为标签定义而必胜**，A/B 结论没有意义。

---

## 4. 分块单位：字符 → Token

改动前：`chunking.py` 用 `len()` 数**字符**，`.env` 里 `RAG_CHUNK_SIZE=800 / RAG_CHUNK_OVERLAP=120`。
改动后：预算单位是 **Token**，由 BGE-M3 自己的 tokenizer 计数，切点落在 Token 边界。

当前生效配置：

```
RAG_CHUNK_SIZE_TOKENS=500
RAG_CHUNK_OVERLAP_TOKENS=50      # 正好 10%
```

实测换算关系（BGE-M3，`XLMRobertaTokenizer`，`model_max_length=8192`）：

- 约 **0.556 token/字符**（纯中文 0.66，中英混排 0.55～0.77）
- 500 token ≈ 1079～1125 字符，所以旧的 800 字符窗口实际只有约 445 token
- 模型窗口 8192 token，800 字符和 500 token 都远不会截断——**≤512 是粒度主张，不是窗口主张**

实现细节（都是踩出来的）：

1. **按 token 下标取出的字符区间，重新计数会多 1 个 token**（513>512）。
   原因是切点落在词内部时 BPE 合并结果会变。
   现在切完以实测值为准回退到预算内，被回退的那个 token 由下一块的 overlap 接住，
   实测"无字符丢失"通过。
2. `rag/worker.py` 里 `split_sections` 现在包在 `asyncio.to_thread` 中：
   分块会真跑 tokenizer，留在事件循环里就是第二个 P0-R2（BM25 曾经的那个坑）。
3. 题库式小节的特例从 3000 字符等长换算成 1600 token
   （`QUESTION_SECTION_CHUNK_TOKENS`），只换单位不换策略。
4. **`AutoTokenizer` 不认 `SENTENCE_TRANSFORMERS_HOME`**，只认 `HF_HOME`/`HF_HUB_CACHE`。
   模型卷里权重实际在 `/models/sentence-transformers/`，`/models/huggingface/` 下只有一个空 `xet`，
   所以 compose 三个用模型的容器都补了 `HF_HUB_CACHE: /models/sentence-transformers`。
   漏掉这一条的后果：`local_files_only=True` 时容器启动直接报找不到模型，
   或者更糟——以为要联网重下。

分块实测（`verify_token_chunking.py`，配置直接从 `get_settings()` 读，10 项全 PASS）：

| 项 | 结果 |
| --- | --- |
| 语料块数 | 195（v9 99 块 + v8 96 块） |
| 每块 token 上限 | 严格 ≤500 |
| 覆盖 | 无字符丢失 |
| 相邻块 | 重叠真实存在 |
| 边界 | 空文本、单块、`overlap=0`、`chunk_size=0`、`overlap≥chunk_size` 均按预期 |

---

## 5. 实测结果

用例 160 条，全部有标签（`unlabeled_cases=0`），融合参数与生产一致（RRF k=60，两路各取 30）。

| 分块口径 | 模式 | 块数 | hit@1 | hit@3 | recall@5 | MRR | empty |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 800 字符 / 120（旧） | lexical | 234 | **0.9313** | 0.9812 | 0.9812 | **0.9566** | 0.0 |
| 500 token / 50（当前） | lexical | 195 | 0.9187 | 0.9750 | **0.9938** | 0.9501 | 0.0 |
| 800 字符 / 120（旧） | hybrid | 234 | **0.8438** | 0.9563 | 0.9594 | **0.9010** | 0.0 |
| 500 token / 50（当前） | hybrid | 195 | 0.8125 | **0.9812** | **0.9906** | 0.8921 | 0.0 |

**结论：换 token 口径不是净涨，是把精度换成了覆盖。**

- hybrid 同条件对照：`hit@1 −3.1pp`、`MRR −0.9pp`，但 `hit@3 +2.5pp`、`recall@5 +3.1pp`。
- 160 条用例里 1 条就是 0.625pp，所以上面这些差异都只有 5～8 条的量级，
  **临界，不能当结论**。要下定论得把用例加到 400+ 或者按用例做配对差检验。
- 还有一个没排掉的混淆：新口径单块约 1000 字符、旧口径 800 字符，块变大 →
  一块能装下的锚点更多，`hit@3/recall@5` 这类覆盖指标天然占便宜，
  而 `hit@1` 因为一块里混了更多无关问题而吃亏。这正是第 3 节说的标签口径问题。

读法：

- **旧口径的 lexical 基线不能作为生产结论**。仓库 runner 把 `--retrieval-mode` 默认设成
  `lexical`，理由写的是"避免 embedding 冷启动"——这本身就是 P0-R5 的症状：
  生产跑的是 hybrid，测试跑的却是单路 BM25。
- 两套口径下 **hybrid 都比 lexical 的 hit@1 低**（旧 0.844 vs 0.931；新 0.813 vs 0.919）。
  原因在融合本身：RRF 只按名次给分，两路等权，
  词面重合极高的题库语料里 BM25 很强，向量路相对弱，
  等权融合就把 BM25 的第一名冲下去了。**这说明 `rag_vector_limit/bm25_limit/rrf_k` 这套
  深度参数从没按指标调过**，是本套数据里最有价值的一条待办。
- 本套用例题面即原文，测不到向量路真正的价值（词面不匹配时的召回）。
  要评"要不要上 hybrid"，得补一批同义改写、口语化的 query。
- hybrid 比 lexical 全面低不代表 hybrid 不该上：它换的是词面不匹配时的召回能力，
  本套用例（题面即查询）测不到这个收益。

> **一组作废的数**：第一次跑对照时旧口径 hybrid 得出 `hit@1 0.2812 / MRR 0.4264`，
> 看着像"改动带来 +55 个百分点"。那是伪数：两套语料共用了同一个 `chunk_id` 生成式
> （`uuid5("文件名:序号")`），新旧序号 0..190 完全撞车，而脚本把两组向量合并成了一个字典，
> 同 ID 的旧块被新块向量覆盖，等于拿新块的向量给旧块打分。
> 修正办法：ID 加口径命名空间（`"char800|"` / `"token500|"`），两组向量各自独立，
> 并加了 `assert not (旧ID集合 & 新ID集合)` 当场拦住。新口径那侧因为覆盖方向恰好正确，
> 数字与单跑一致，可以保留。

指标定义（`rag_metrics.metrics`）：

- `hit@k`：前 k 条里**有没有**相关块（0/1，再对用例取均值）
- `recall@k`：前 k 条覆盖了多少比例的相关块
- `MRR`：第一条相关块的位置取倒数
- `empty`：召回空列表的用例比例

---

## 5.5 引用出处（文档名 / 页码）

字段本来就一路在传，缺的是**喂给模型的那一行**：

| 环节 | 文档名 | 页码 |
| --- | --- | --- |
| `rag/retrieval.py:348,376` `RetrievedChunk` | `metadata["filename"]` | `source_page`（取自 PG 权威行，不是 Qdrant payload） |
| `app/rag_eval_api.py:211,215` `/api/rag/eval` 出参 | 有 | 有 |
| `agent/worker.py:968,1010` 返回前端的 citations | 有 | 有 |
| `agent/worker.py` 拼给模型的 evidence 行 | 有 | **原来没有** |

原来那一行是 `[1] 文件名 score=0.87: 预览`，
模型看不到页码，所以写不出"见第 7 页"这种可核对的引用——
即使前端 citations 里有页码，答案正文也不会带。
现在补成 `[1] 文件名 第7页 score=0.87: 预览`，并把指令改成
"在答案中标注依据来自哪个文件名以及页码（若有）"；
`source_page is None` 时不拼页码，避免出现"第 None 页"污染上下文。

实测方式（本机没有 langgraph，跑不了整个 agent worker，所以**把改后的那段源码原样抽出来重放**）：

```
[1] 服务_rag_v3.pdf 第7页 score=0.87: 第一页内容片段
[2] 梁向东_项目经历专项面试.docx score=0.61: 无页码文档片段
```

报表侧同步补了出处：

- `metrics_report.json` 每条用例新增 `top_citations`（`filename` / `source_page` / `preview`）
- `labels_audit.json` 的相关块条目加 `source_page`
- runner 的 CSV 加 `top_page` 列，MD 表在 Top file 后附"第N页"

**一个改不掉的事实**：`parser.py` 只给 PDF 记页码，docx/xlsx 的段落流没有页概念，
所以本项目里上传的 docx 语料 `source_page` 恒为 `null`——上面实测里那块 docx 就是 null。
要让 Word 文档也有页码，得上版面解析（按分页符/渲染定位），那是 P2，没做。

---

## 5.6 生产在线口径（真实 API 全链路，2026-10-07）

前面所有数都是离线 harness。这一节是把容器真的拉起来、按生产链路走一遍的数：
`postgres + redis + qdrant + api + rag-worker + agent-worker + cleanup-worker`，
语料是那份 docx 走 `POST /api/knowledge/bases/{id}/documents` 真实入库，
检索走 `POST /api/rag/eval`（内部就是 `hybrid_retrieve`）。

**入库对账**：Qdrant `rag_ed0a…` 点数 **195**，PG 侧 96 + 99 = **195**，
与第 4 节离线 500 token / 50 口径的块数完全一致——分块改动在容器里成立。

两档的原始报告都在仓库里（`evaluation/results/`，csv/json/md 各一份，可核对到每条用例）：

- `rag_eval_20261007T142102Z.*` —— 在线 hybrid（`use_reranker: false`）
- `reranker_tier_20261007T141854Z.*` —— 在线 hybrid + reranker（同一批用例）

`summary.rank_metrics` 里就是下表的 hit@k / MRR / 名次直方图 / miss 用例 ID。

| 口径 | hit@1 | hit@3 | hit@5 | MRR | empty |
| --- | ---: | ---: | ---: | ---: | ---: |
| 离线 hybrid（500t/50，候选 30） | 0.8125 | 0.9812 | 0.9938 | 0.8921 | 0 |
| 在线 hybrid（同配置，真实 API） | **0.7625** | 0.9812 | 0.9938 | 0.8664 | 0 |
| 在线 hybrid + reranker（同一批用例） | **0.95** | 1.0 | 1.0 | **0.9729** | 0 |

- runner 报的 `pass_rate 99.4%`（159/160）判的是"锚点在返回的 5 条里"，等价于 **hit@5**，
  不是检索质量本身；上表的 hit@1/MRR 是从报告 JSON 的 `top_results` 按真实排名反推的。
- hit@3 / hit@5 两行一模一样，唯一差异全在首位精度：在线低 5pp（8 条用例从 rank1 掉到 rank2）。
  在线链路比离线多做了两件事——入库时给块补关键词、PG 权威复核（7 个谓词）——
  两者都会改变 BM25 词表与候选集合，具体是哪一侧还没定位。
- 唯一 miss 的那 1 条（`梁向东_项目经历专项面试-01-018`）已核实**不是分块问题**：
  锚点 `27. RAG Evaluation 怎么设计？` 在库里确实存在（`like` 命中 1 块），
  只是没进前 5——是排序 miss，不是覆盖 miss。

**reranker 是本项目目前测到的最大质量杠杆**（同一批 160 条用例、同一套入库语料，
只把 `use_reranker` 打开）：

| 名次直方图 | rank1 | rank2 | rank3 | rank4 | rank5 | miss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 不开 reranker | 122 | 27 | 8 | 1 | 1 | 1 |
| 开 reranker | 152 | 6 | 2 | 0 | 0 | 0 |

- `hit@1` 从 0.7625 → **0.95（+18.75pp）**，MRR 0.8664 → **0.9729**，
  160 条**一条 miss 都不剩**，`pass_rate` 到 100%。
  27 条从 rank2 被提到 rank1，剩下的 rank≥3 也几乎全被拉到首位。
- 这直接改变了第 8 节原来那条判断：**"CPU 太慢所以 reranker 不可用"不成立**，
  它换来的增益比分块、融合调参任何一项都大。真正的问题是部署形态，
  不是该不该开（见第 8 节第 7 条）。

**分段延迟实测**（P0-R3 加的 `stage_ms` 直接派上用场）：

| 阶段 | 不开 reranker | 开 reranker |
| --- | ---: | ---: |
| embed | 152～359 ms | 152～359 ms |
| vector_search | 72 ms | 72 ms |
| bm25_search | 544～698 ms | 544～698 ms |
| rerank（12 候选） | — | **19 920～21 673 ms** |
| total（单条 query） | 0.6～0.7 s | **21～22 s** |

160 条全量的 `elapsed_ms`（含 HTTP 与评测侧开销，两份正式报告实测）：

| 口径 | min | p50 | p90 | max |
| --- | ---: | ---: | ---: | ---: |
| 在线 hybrid | 470 ms | 505 ms | 544 ms | 677 ms |
| 在线 hybrid + reranker | 16 062 ms | **18 863 ms** | 21 768 ms | 120 977 ms（5 条 >30 s） |

`bge-reranker-v2-m3` 在纯 CPU 容器里给 12 个候选打分要 20 秒，占整条链路 94%，
而它同时把 `hit@1` 抬了 18.75 个百分点。所以这是一个**部署取舍**，不是"能不能开"：
要么上 GPU / 换小模型（bge-reranker-base、cross-encoder 蒸馏版），
要么把重排从同步问答链路里摘出去（批处理、或只对最终 top-N 之前的候选重排）。
本轮按用户口径只做测量与记录，没动代码。

**顺带挖出两个生产缺陷**（都只记录，没改）：

1. **同一个"返回几条"在项目里有三个值**：`hybrid_retrieve` 按配置返回
   `rag_final_limit=6`，`app/rag_eval_api.py:185` 硬编码 `hits[:5]`，
   `agent/worker.py:1014` 又硬编码 `hits[:3]`。也就是说**真喂给模型的证据只有 top-3**，
   对应的生产 hit@1 是 0.7625、hit@3 是 0.9812，而评测按 top-5 打分。
   评测口径比生产口径宽，这类偏差会让人高估线上表现。
   （开 reranker 时 hit@3 = 1.0，top-3 截断不再有信息损失，这也算 reranker 的一条附带收益。）
2. **`document_chunks.token_count` 全库为 0**。列在 `infrastructure/models.py` 里有，
   但 `TextChunk`（`rag/chunking.py:26`）不带 token 字段，worker 入库时也没写。
   既然分块预算已经改成 Token，这个字段就是唯一的容量审计入口，
   空着意味着无法按 token 做上下文窗口成本和超长告警。

另外"模型是否真会在答案里标注页码"属于模型行为，本机没跑过 LLM，**未验证**。

---


## 6. 怎么跑

### 6.1 离线指标（需要原始语料文件）

本机 `.venv-eval` 已具备 torch / sentence_transformers / transformers；
`jieba` 不在该 venv 里，用 `PYTHONPATH` 指到外部目录即可（不装进共享 venv）。

```bash
cd D:/product/knowledgeops-console
PYTHONIOENCODING=utf-8 \
PYTHONPATH=".;<jieba所在目录>" \
HF_HUB_CACHE="<模型缓存目录>" HF_HUB_OFFLINE=1 \
SENTENCE_TRANSFORMERS_HOME="<模型缓存目录>" RAG_MODEL_LOCAL_FILES_ONLY=true \
.venv-eval/Scripts/python.exe evaluation/rag_metrics.py \
  --corpus-dir "<语料目录>" \
  --files "<文件名>" \
  --cases evaluation/rag_eval_cases_v3.json \
  --ks 1,3,5 --mode hybrid
```

产出 `metrics_report.json`（含每条用例明细）和 `labels_audit.json`（每条用例的相关块及预览，人工抽查用）。

耗时（CPU）：嵌 195 块约 4.5 分钟，160 条 query 约 3.5 分钟。lexical 模式不加载模型，约 1 分钟。

### 6.2 在线断言（打 API）

```bash
# 宿主机：runner 的 --retrieval-mode 默认还是 lexical，要对齐生产必须显式写 hybrid
python evaluation/rag_eval_runner.py \
  --base-url http://localhost:8000 \
  --cases evaluation/rag_eval_cases_v3.json \
  --retrieval-mode hybrid

# reranker 档（第 5.6 节那组 0.95/MRR 0.9729 的数）再加一个开关
#   --use-reranker    单条 query 从 ~0.5 s 变成 ~19 s，160 条串行约 54 分钟
#   --timeout 180     否则长尾用例（有 5 条 >30 s）会超时判失败

# 容器里（compose 已加 eval profile，指向服务名不是 localhost，命令里已经带了 hybrid）
docker compose --profile eval run --rm rag-eval
```

`--fail-under 0.8` 决定退出码，可以直接挂 CI。
注意：v3 用例的前提是那两份文档已经**入库并发布**，否则全条都会失败——那是数据没进去，不是检索退化。
报告落在 `evaluation/results/`（`--output-dir` 默认值；容器里靠 bind mount 回到宿主同一目录）。
跑 reranker 档前先把服务进程预热（前几条会带上模型加载时间），不然首条延迟是冷启动不是链路延迟。

### 6.3 端口

- `8000` 是 FastAPI，评测打这个；`/health` 目前是 404，别拿它当就绪探针，用 `/docs` 判就绪
- `8080` 是前端 nginx，**已经在这台机器上起来了**（`node:22-alpine` + `npm install` 走的是镜像站转法，见第 7 节）；
  评测**不要**打 8080，`/api/*` 之外会命中 SPA 的 `try_files`，未登录时拿到的是 FastAPI 的 401 而不是 502

---

## 7. 容器启动流程（2026-10-07 实测通过，逐步可复现）

```bash
docker compose build api                       # 镜像 9.96 GB，python:3.11-slim + requirements
docker network connect knowledgeops-console_default qdrant
docker compose up -d api                       # postgres/redis 先 healthy
docker compose exec -T api alembic upgrade head # 0001_initial -> 0002，两步都过
docker compose exec -T api python -m app.bootstrap   # 建租户 + admin（main.py 不会自动调）
docker compose up -d rag-worker agent-worker cleanup-worker
docker compose build frontend && docker compose up -d frontend   # 基础镜像见第 7 节的镜像站转法
```

踩过的四个坑，都会让"看起来起来了"其实没起来：

1. **`app/bootstrap.py` 不在 `main.py` 的启动路径里**，不手工跑就没有账号，
   前端和 `/api/rag/eval` 全部 401。
2. **迁移 0002 原来会整体回滚**：`0001_initial` 是按活的 `Base.metadata` 建表的，
   模型里已经带 `scope` 列，0002 又无条件 `ADD COLUMN` → `DuplicateColumn`，
   Alembic 的事务把 0001 一起回滚，`agent_db` 落得一张表都不剩。
   现在 0002 用 inspector 先判列是否存在（`alembic/versions/0002_add_knowledge_base_scope.py`）。
   生产仍然缺一个"发布前自动迁移"的步骤，compose 里没有。
3. **`AutoTokenizer` 不认 `SENTENCE_TRANSFORMERS_HOME`**，只认 `HF_HOME` / `HF_HUB_CACHE`，
   所以 compose 给三个模型服务加了 `HF_HUB_CACHE: /models/sentence-transformers`，
   否则容器里会去联网下载 BGE-M3。
4. **`/health` 是 404**，别用它做就绪探针；`/docs` 返回 200 才算起来了。

模型与外部服务：

- BGE-M3 / `bge-reranker-v2-m3` 权重在命名卷 `project_hf_models`。容器里实测
  `get_settings().rag_model_local_files_only is True`（这是 `infrastructure/config.py:157` 的默认值，
  `.env` 里并没有写这个变量），加载走本地缓存、不联网。
- `qdrant` 在宿主机只发布 `127.0.0.1:6333`，容器访问不到 `host.docker.internal:6333`，
  所以把它挂进 `knowledgeops-console_default` 并把 `QDRANT_URL` 覆盖成 `http://qdrant:6333`。
  检索日志里 `vector_hits=30` 已经证明这条路是通的。
- `ollama`（`bge-m3`、`qwen3:1.7b`）从容器内经 `host.docker.internal:11434` 可达，实测 200。
- 前端 `frontend` 服务**已经起来**（`8080`），但取基础镜像绕了一道：
  Docker Hub 在这台机器上直连不通——`docker pull node:22-alpine` 与 BuildKit 的
  `failed to resolve source metadata` 都是同一个 `registry-1.docker.io:443` 超时，
  而本机 Clash（`127.0.0.1:7897`）到 Docker Hub 也是 000。
  实际用的是镜像站转一手再打回标准 tag：

  ```bash
  docker pull docker.m.daocloud.io/library/node:22-alpine
  docker pull docker.m.daocloud.io/library/nginx:1.27-alpine
  docker tag docker.m.daocloud.io/library/node:22-alpine  node:22-alpine
  docker tag docker.m.daocloud.io/library/nginx:1.27-alpine nginx:1.27-alpine
  docker compose build frontend        # npm registry 从构建容器里是直连可达的，不用代理
  ```

  `Dockerfile` 的 `FROM` 不用改，但**换机器要重做这三步**（或在 Docker Desktop 里配镜像/代理），
  否则构建会卡在取 base image。
- 前端实测：`GET /` 200、`/assets/index-*.js` 1.06 MB 且 MIME 正确；
  `GET /api/knowledge/bases` 经 nginx 反代返回 FastAPI 自己的
  `{"detail":"未登录或登录状态已失效"}` + `www-authenticate: Bearer`（不是 502/404），
  说明 `nginx.conf` 里 `proxy_pass http://api:8000` 这条服务名链路是通的。
  注意 SPA `try_files ... /index.html` 会让**不存在的资源也返回 200**，
  看状态码判断资源是否打进去了并不可靠。
- 宿主上 `frontend/dist`、`frontend/node_modules` 现在都不在（且被 `.gitignore` 忽略），
  构建产物只在镜像里。想在本机 `vite preview` 得先装 node——这台机器没有 node/npm。


评测产物落在宿主：`docker-compose.yml` 给 `api` 和 `rag-eval` 都挂了
`./evaluation/results:/app/evaluation/results`，所以容器里跑出来的报告直接进仓库的
`evaluation/results/`（csv/json/md，时间戳命名），和 README 写的输出目录一致。
**不要再往 `uploads/` 里放评测产物**——那是 bind 目录，会被 `git add -A` 扫到，
而且里面是真实语料，报告容易连带泄漏个人信息。


---

## 8. 还没做的

1. **标签改成答案区间**（第 3 节的缺陷）——改分块策略之前必须先做，否则 A/B 自证。
2. metrics 结果已改成时间戳报告，但**多次运行之间没有对比视图**，改一版还得手工 diff JSON。
3. 分块策略 A/B：当前字符窗口 vs 500 token vs 按题分块，只测了前两档。
4. 版面/表格/图片：`parser.py` 把 docx 表格段落一律追到正文末尾、PDF 不做 OCR，
   真实上传文档的表格语义还没进评测。
5. **统一截断口径**（5.6 发现的第 1 条）：`rag_final_limit` / eval 的 `[:5]` /
   worker 的 `[:3]` 应该收成一个配置项，评测必须按生产实际喂给模型的名次打分。
6. **回填 `document_chunks.token_count`**（5.6 发现的第 2 条）：
   `TextChunk` 加 token 字段，worker 入库时写入，容量与超长告警才有数据可用。
7. **reranker 落地形态**（5.6 延迟表）：20 s/query 不能上生产，
   要么换小模型/GPU，要么离线评测确认增益后再决定要不要为它改部署。
8. 在线与离线的 `hit@1` 差 5pp，**差异来源还没归因**（补关键词 vs PG 复核）。
