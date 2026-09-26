# 重复测试与分页验证（2026-09-26）

## 本轮做了什么

1. **有上限的分页**：扫描到底后，使用观察到的同主机下一页链接；要求 rel=next 或分页区域语义，不把普通“下一步”按钮当作翻页。最多翻两次，不生成 URL。
2. **等待与防循环**：等待 URL 或结果卡片变化，支持网址不变的异步加载；记录已访问链接和结果集合，发现循环就停止。读操作可以等待，已执行的翻页点击不自动重放。
3. **修复搜索建议跳转**：建议链接直接打开目标文章时，保留已完成的搜索要求，避免回头重复填写搜索框。
4. **链接兼容性**：ARIA role=link 的控件不一定具有原生 a 元素的 relList，新增类型保护。
5. **可重复评估工具**：固定目标、独立核验、重复执行、保留错误与截图；输出目录存在时拒绝覆盖。支持不调用 API 的原始页面证据复核。

## 真实网站重复测试

使用用户提供的本地 API 入口，请求 cx/gpt-6-luna，响应 gpt-6-luna；本机 Laya 负责局部选择。4 种指令各执行两次，最终 8/8 同时满足 DONE 和独立页面检查。

| 指令 | 通过次数 | 任务耗时范围（秒） | 单次规划 API 调用 |
|---|---:|---:|---:|
| Gödel 文章，英文指令 | 2/2 | 4.174–5.017 | 1 |
| BERT 论文，英文指令 | 2/2 | 9.240–10.898 | 2 |
| BERT 论文，中文指令含英文标题 | 2/2 | 10.740–13.811 | 2 |
| Ada Lovelace，中文指令 | 2/2 | 3.540–3.863 | 1 |

这不是 8 个独立题目：BERT 的中英文指令指向同一篇论文。两轮共用浏览器配置、各自创建任务标签页，存在缓存与页面状态影响。耗时是 Agent 任务计时，另存的 wall_ms 包含初始化与验证开销。

## 失败没有被删除

初轮经正确核验后为 7/8，Wikipedia 有一次真实失败：点击自动补全建议后已经进入文章，却被当成仍需提交的搜索值，引起重复搜索。本地 suggestion 场景专门复现并验证修复：仅填写一次、点击一次，就在文章正文结束。

初版 BERT 检查器误用了复数措辞 language representations，而页面摘要实际是 language representation model。这是检查器错误，不是模型失败。原始结果保持不变，修正后通过 --rescore 对同一批保存页面重新核验，未额外调用 API，也未重新抽样。最终一轮使用修正后的检查器直接运行，8/8 通过。

查票首次回归发现观察层 relList 类型错误，发生在规划前，API 调用为零；修复后查票通过全部九项核验。

## 分页与原有功能回归

| 本地场景 | 核验结果 |
|---|---|
| 目标在第三页 | 两次翻页后打开正确正文 |
| 同网址延迟更新 | 等待新结果后继续，未重复点击翻页 |
| 循环分页 | 在有上限的尝试后 BLOCKED，未误报完成 |
| 搜索建议直接跳转 | 一次填写后打开正文，无第二次搜索 |

以上本地场景使用真实 Laya、预设目标计划，不调用规划 API；测试服务器仅绑定环回地址，结束后关闭。测试页面包含一个非原生 role=link 控件，用于覆盖兼容性问题。

原有 travel、research、zh 三个本地示例均通过。查票真实 API 回归耗时 13.639 秒，13 个动作、1 次规划 API 调用，九项核验通过。

156 项离线测试、Ruff、snapshot.js 与 app.js 语法检查、构建和差异检查通过。

## 如何复现

从仓库根目录执行；密钥通过隐藏输入或进程环境提供，不写入命令或文件。以下示例使用本地兼容入口：

```bash
PYTHONPATH=. uv run --extra local --offline python examples/evaluate_stability.py \
  --base-url http://localhost:20128/v1 --model cx/gpt-6-luna --prompt-key \
  --repeat 2 --output artifacts/my-new-stability-run

PYTHONPATH=. uv run --extra local --offline python examples/evaluate_pagination.py \
  --output artifacts/my-new-pagination-run

PYTHONPATH=. uv run --extra local --offline python examples/evaluate_stability.py \
  --rescore artifacts/my-new-stability-run --output artifacts/my-new-recheck
```

需先启动专用测试浏览器（默认 CDP 9334）；原有本地示例另需 8770 服务。离线推理权重必须已缓存。--offline 指依赖解析，不代表真实网站任务不访问网络。

## 边界与下一步

真实网站重复测试覆盖两个网站、三项内容；两次重复不足以估计长期可靠性。新增跨页能力目前经过受控本地场景验证，尚不能宣称适用于任意真实网站。卡片结构、分页标识、验证码及超过预算的长结果列表仍可能造成失败。

下一步应将真实分页网站加入固定评估集，并测试目标不存在、同名条目、页面变化等负例；继续区分正确停止与完成任务。

本轮所有开发与验收（包括初轮失败）累计 27 次规划 API 请求，响应报告共 63,261 tokens；复核不产生新 API 请求。没有定价依据，未换算费用。

[机器可读测量记录](stability-20260926-measurement.json)。原始轨迹与截图保留在本地 artifacts/stability-* 和 artifacts/pagination-*。
