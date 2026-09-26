# 作者归属检查：2026-09-26

## 修复的问题

上一轮只检查作者名字是否出现在卡片和正文中。本轮实际复现了两个错误的 DONE：

- 错误作者撰写的文章，只在正文讨论目标作者。
- 列表标注目标作者，但打开后的详情页署名为另一个人；正文仍提及目标作者。

原始失败保留在 `artifacts/authorship-before-20260926`。

## 修改

- 计划新增可选 `authors`，与普通 `identity_terms` 分开。规划器只提取用户明确要求的作者，修复计划不能丢掉初始作者条件。
- DOM 观察增加 `authors` / `result_authors`，记录文本和证据来源：可见作者标签、署名标记、定义列表、表格作者字段。支持通用的 `rel=author`、`itemprop=author`、author/authors/byline 类名；不包含网站专用分支。
- 作者条件须在候选卡片和详情页各自满足。正文偶然提到某人不再足够。
- 作者与要求冲突的详情页可通过观察到的返回动作退回；错误链接进入排除集合，最多返回两次。没有可用返回动作时停止。
- 隐藏元素、导航、侧栏、页脚及不包含当前主标题的嵌套文章不作为当前页面作者证据。

## 测试结果

| 场景 | 规划方式 | 实际结果 |
| --- | --- | --- |
| 同名、两位作者 | 用户 API + Laya | 1 次规划，正确文章，1 个动作 |
| 只有错误作者 | 用户 API + Laya | 3 次规划，BLOCKED，未打开文章 |
| 正文仅提及目标作者 | 用户 API + Laya | 3 次规划，BLOCKED，未打开文章 |
| 列表和详情作者冲突 | 用户 API + Laya | 3 次规划，打开后返回并停止，4 个动作，无误报完成 |
| arXiv：首页找 Jacob Devlin 的 BERT 论文 | 用户 API + Laya | 2 次规划，9 个动作，12.493 秒，正确摘要页 |
| Wikipedia：Gödel | 用户 API + Laya | 1 次规划，2 个动作，4.400 秒，独立检查通过 |
| Google Flights：苏黎世→伦敦，2026-10-20 | 用户 API + Laya | 1 次规划，13 个动作，15.069 秒，9 项检查通过 |
| travel / research / zh | 预设计划 + Laya | 3 项通过，0 次规划 API |

前四项使用本地构造页面；后三个网站任务是真实页面。BLOCKED 表示正确拒绝，不能计为成功完成用户查找任务。各样本只有一次完整 API 验证，没有统计意义上的成功率结论。

中间版本 `authorship-after-20260926` 已不再误报，但作者冲突样本仍产生 39 个动作；加入返回处理后，同类完整 API 测试缩短为 4 个动作。

本轮 **176 项离线测试通过**；Ruff、JS 语法、构建、差异格式检查通过。

API 请求模型为 `cx/gpt-6-luna`，响应模型为 `gpt-6-luna`。共 **14 次规划请求、27,157 tokens**（按响应 usage 合计）。实际费用未知。未调用 Qwen；密钥仅通过隐藏输入传入进程。

## 证据与复现

汇总：[authorship measurement](authorship-20260926-measurement.json)。完整轨迹位于：

- `artifacts/authorship-before-20260926`
- `artifacts/authorship-after-20260926`
- `artifacts/authorship-identity-api-20260926`
- `artifacts/authorship-live-20260926`
- `artifacts/authorship-fixtures-20260926`

```sh
PYTHONPATH=. uv run --extra local --offline python examples/evaluate_boundaries.py \
  --cases namesake wrong_author mention misleading_detail \
  --base-url http://localhost:20128/v1 --model cx/gpt-6-luna \
  --output artifacts/new-author-check

PYTHONPATH=. uv run --extra local --offline python examples/evaluate_stability.py \
  --base-url http://localhost:20128/v1 --model cx/gpt-6-luna --prompt-key \
  --cases bert_author wikipedia flights --repeat 1 --output artifacts/new-author-web-check
```

## 仍然存在的边界

- 作者证据来自网站的标注，不能证明真实作者身份；标注本身可能错误。
- 目前匹配名字的连续规范化文本，不处理所有姓在前的格式、缩写、别名和跨语言姓名；可能保守拒绝。
- 对没有明确署名标记的页面，正文中出现姓名也不会满足作者条件。
- 初始规划仍可能漏掉条件。已观察到四个本地目标和真实 BERT 目标都提取了 `authors`，但不保证其他目标永不遗漏。
- 旧的预设计划若只含 `identity_terms`，仍采用普通文本条件。需要将作者条件迁移至 `authors`；本轮完整 API 测试使用新规划格式。
- 本轮只验证新 CLI 进程；未重启 8770 服务，未提交或推送 GitHub。
