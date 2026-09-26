# 作者姓名边界与倒序：2026-09-26

## 原始问题

实际复现并保留了两个失败：

- 单人姓名 `Researcher, Desired, 1900-1980` 无法匹配 `Desired Researcher`，正确目标被 BLOCKED。
- 多人署名 `Other Desired, Researcher Else` 被去掉标点后，错误匹配 `Desired Researcher`，返回错误 DONE。

原始轨迹：`artifacts/name-order-before-20260926`。

## 修改

- 在规范化之前保留每位作者的边界，不再对子串进行姓名匹配。
- 观察结果区分单人证据和多人文本；作者区域内独立链接分别保留为单人证据。
- 仅对明确的单人字段支持 `姓, 名` 倒序，并忽略尾部规范的生卒年范围。
- 不把名字缩写扩展成任意同首字母名字：`J. Smith` 不足以确认 `John Smith`。同样不自动忽略 Jr.、中间名或猜测别名。
- 生产代码没有网站、书名、作者姓名专用规则。

## 验收

预设计划、真实 Laya 的 6 项本地验证全部符合预期：姓名倒序和同名正确作者成功；跨作者拼接、缩写不充分、正文提及均停止；列表与详情作者冲突会返回并停止。这些拒绝不是成功完成查找任务。

使用用户 API 的真实页面验证：

| 场景 | 结果 | 规划调用 | 浏览器动作 |
| --- | --- | --- | --- |
| Gutenberg：Lewis Carroll 姓名倒序 | 详情页独立核验通过 | 1 | 0 |
| arXiv：Jacob Devlin 的 BERT 论文 | 从首页找到正确摘要页 | 2 | 9 |
| Wikipedia：Gödel | 正确文章 | 1 | 2 |
| Google Flights：苏黎世到伦敦，2026-10-20 | 9 项检查通过，没有预订 | 1 | 12 |

Gutenberg 测试直接从书籍详情页开始：规划器提取作者，规则核验已有页面，**0 次 Laya 推理**，不能当作端到端检索成功。其真实字段为 `Carroll, Lewis, 1832-1898`，计划作者为 `Lewis Carroll`。

原有 travel / research / zh 三项回归通过。离线 **190 项测试通过**；Ruff、JS 语法、构建、差异格式检查通过。

本轮规划接口合计 **5 次请求、11,404 tokens**，请求 `cx/gpt-6-luna`，响应 `gpt-6-luna`。实际收费未知。预设计划和离线测试不调用规划 API。

## 复现与证据

汇总见 [measurement](name-order-20260926-measurement.json)。完整轨迹位于 `artifacts/name-order-before-20260926`、`name-order-after-20260926`、`name-order-live-20260926`、`name-order-fixtures-20260926`。

```sh
PYTHONPATH=. uv run --extra local --offline python examples/evaluate_boundaries.py \
  --cases inverted author_collision initials namesake mention misleading_detail \
  --output artifacts/new-name-boundaries

PYTHONPATH=. uv run --extra local --offline python examples/evaluate_stability.py \
  --base-url http://localhost:20128/v1 --model cx/gpt-6-luna --prompt-key \
  --cases gutenberg_author bert_author wikipedia flights --repeat 1 \
  --output artifacts/new-name-web
```

仍然依赖网站署名标记是否准确；多人文本中缺少分隔符、姓名别名、特殊排版可能导致保守停止。姓名匹配不等于现实人物身份认证。本轮单次样本不构成总体成功率。

改动未提交或推送；原 8770 服务未重启，本轮验证通过新 CLI 进程执行。
