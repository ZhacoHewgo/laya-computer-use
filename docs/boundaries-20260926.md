# 翻页与同名条目：2026-09-26

## 本轮发现与修复

1. **真实网站翻页漏识别**：Gutenberg 的 Next 链接没有 `rel=next`，也不在已识别的分页容器中；它通过 `title="Go to the next page of results."` 说明用途。修复前读完第一页就 BLOCKED。现在同时要求 Next 标签和明确的下一页说明，继续保留同站点、观察到的链接、最多两次翻页限制。生产代码没有 Gutenberg 域名或选择器分支。
2. **同名不同作者误报**：本地列表只有错误作者的同名文章时，原策略点击后返回 DONE。新增可选 `identity_terms`，规划器提取用户明确要求的作者等身份短语；结果卡片及详情页均须包含这些短语。修复计划不能丢掉初始身份条件。

## 验收结果

| 场景 | 规划方式 | 结果 |
| --- | --- | --- |
| 目标不存在 | 预设计划、真实 Laya | 翻页两次后 BLOCKED，未打开文章 |
| 同名、两位作者 | 预设计划、真实 Laya | 打开指定作者的文章 |
| 只有错误作者 | 预设计划、真实 Laya | BLOCKED，未打开文章 |
| Gutenberg：Alice's Adventures Under Ground | 预设计划、真实 Laya | 翻页一次，打开 `/ebooks/19002`，核对 Lewis Carroll |
| 同名、两位作者 | GPT 实时规划、真实 Laya | 1 次规划，正确提取作者并打开文章 |
| 只有错误作者 | GPT 实时规划、真实 Laya | 初始规划及两次修复，共 3 次规划，正确拒绝 |
| Wikipedia：Gödel | GPT 实时规划、真实 Laya | 通过独立页面检查，1 次规划 |
| Google Flights：苏黎世→伦敦，2026-10-20 | GPT 实时规划、真实 Laya | 9 项检查通过，1 次规划；没有选择或预订航班 |
| travel / research / zh | 预设计划、真实 Laya | 3 项通过 |
| 原分页：normal / delayed / cycle / suggestion | 预设计划、真实 Laya | 4 项符合预期，cycle 是正确停止 |

离线测试 **165 passed**；Ruff、两份 JS 语法检查、构建、差异格式检查通过。

实时规划使用用户指定的 loopback API 和 `cx/gpt-6-luna`，返回模型名为 `gpt-6-luna`。本轮共 6 次规划请求，响应记录合计 10,164 tokens。密钥通过隐藏输入传递，仅驻留进程环境。API 实际费用未知；其余预设计划测试没有规划接口调用。

## 证据

汇总见 [boundary measurement](boundaries-20260926-measurement.json)。本地完整轨迹保存在以下目录（artifacts 被 Git 忽略）：

- `artifacts/boundaries-before-20260926`：原始翻页失败，同名测试当次选对。
- `artifacts/boundaries-wrong-author-before-20260926`：错误作者被误报 DONE 的原始记录。
- `artifacts/boundaries-after-20260926`：4 个新增执行场景。
- `artifacts/boundaries-identity-api-20260926`：2 个作者条件的完整规划链路。
- `artifacts/boundaries-live-20260926`：Wikipedia / Flights。
- `artifacts/boundaries-fixtures-20260926`、`artifacts/boundaries-pagination-20260926`：原有回归场景。

## 复现

```sh
PYTHONPATH=. uv run --extra local --offline python examples/evaluate_boundaries.py \
  --output artifacts/new-boundary-run

PYTHONPATH=. uv run --extra local --offline python examples/evaluate_boundaries.py \
  --cases namesake wrong_author --base-url http://localhost:20128/v1 \
  --model cx/gpt-6-luna --output artifacts/new-identity-run
```

第二条命令会隐藏询问密钥。输出目录必须不存在，避免覆盖失败证据。需要原有独立浏览器运行在 CDP 9334。

## 能力边界

- Gutenberg 测试从现有搜索列表开始，预设计划并禁用自动重搜，专门验证翻页执行；不是从首页自然语言任务开始的完整规划测试。
- 作者消歧与缺失目标是本地构造页面；真实网站只新增了一个分页例子，不能据此宣称泛化成功率。
- `identity_terms` 是规范化文本匹配，不理解作者关系；正文中偶然提到某人，也可能满足检查。卡片缺少作者信息、姓名缩写或跨语言变体可能导致保守停止。
- 初始规划器仍可能遗漏条件；两次真实规划均提取成功不代表永不遗漏。BLOCKED 表示未证实可完成，不证明整个网站不存在目标。
- 最多两次翻页、每页最多 24 次滚动、整个任务最多 60 个动作；更深目标仍可能无法达到。
- 本轮修改已在新 CLI 进程验证，未重启既有 8770 界面服务，也未提交或推送 GitHub。
