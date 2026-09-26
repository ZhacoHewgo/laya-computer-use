# 本轮修复与回归 · 2026-09-26

## 结论与范围

最终一轮中，9 个本地场景通过独立检查；Python 文档、Wikipedia、Google Flights 三个真实网站任务仍未通过。全部推理使用本机缓存的 Laya multilingual + Qwen3-1.7B-4bit，关闭 thinking；没有付费模型 API 调用。

这些是开发中反复使用的固定案例，每场景表格记录最后一次有界运行，不是留出测试集，也不能解释成通用成功率。期间发生过提示改动造成退步，未删除本机中间失败轨迹。耗时包含规划、决策和浏览器操作，不含模型加载；本表关闭截图。

## 实际改动

- **提交与打开详情分开**：观察原生表单关系与提交按钮，只把提交控件放进提交候选，优先使用刚编辑过的表单。填写、勾选或下拉变更后，先提交，再等待新结果文字或页面变化；无反馈时停止，不重新提交。详情按钮不再因为模型分数高就充当提交按钮。
- **校验计划**：将泛指“车次结果”的 open 归为结果页条件；拦截把打开条目写成填表的已知结构错误，最多重试三次并给出错误反馈。对观察到的 View/Read/Open 前缀做标题归一。只移除 JSON 外部代码围栏，不从多段输出中猜选一段 JSON。
- **跨语言候选选择**：规划器能看到页面条目名称。没有文字重合时，Laya 根据原始用户请求做语义选择，并允许选择 none；打开后使用所选标签核对页面标题。
- **浏览器读取恢复**：导航只发一次；上下文切换导致的读取失败可重读。保留真正的脚本错误，初始化失败关闭所创建的标签。下拉操作可能已经执行时，明确报错，不自动重复写入。
- **保留计划来源**：规划原始响应、重试次数、规则修正、初始计划和后续工作计划分别记录。补充搜索条件不会再改写此前的计划记录。
- **修正测试误报**：Python 教程目录包含章节名，不能据此判断打开成功；现在检查章节路径、主标题和正文。酒店检查详情页回显的城市与筛选；车票检查字段、复选框和实际结果行。

“收到提交反馈”仍是通用启发式，不能证明任意网站已应用所有条件；最终结果必须由独立检查确认。

## 最终测量

| 场景 | 独立检查 | Agent 状态 | 动作 | Laya 调用 | 规划调用 | 秒 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 杭州→上海直达车票 | 通过 | done | 6 | 2 | 1 | 3.351 |
| Lisbon / Design / Casa Flora | 通过 | done | 6 | 2 | 1 | 2.189 |
| 英文描述→浏览器文章 | 通过 | done | 1 | 1 | 1 | 0.952 |
| Copenhagen / Design / The Glasshouse | 通过 | done | 6 | 2 | 1 | 2.134 |
| Lisbon / Nature / Serra Lodge | 通过 | done | 5 | 2 | 1 | 1.985 |
| 中文描述→置信度英文文章 | 通过 | done | 1 | 1 | 2 | 1.735 |
| 英文标题→置信度文章 | 通过 | done | 1 | 1 | 2 | 1.986 |
| 北京→天津，包含中转 | 通过 | done | 5 | 4 | 1 | 2.986 |
| 已勾选直达→取消并重新搜索 | 通过 | done | 2 | 4 | 1 | 3.012 |
| Python Data Structures 章节 | 未通过 | blocked | 3 | 10 | 1 | 2.249 |
| Wikipedia 哥德尔文章 | 未通过 | blocked | 4 | 12 | 1 | 5.602 |
| Google Flights | 未通过 | blocked | 21 | 15 | 1 | 8.637 |

取消勾选一行只计第二个任务；准备已勾选状态的前置任务另存 `toggle_setup.json`。规划调用大于 1 表示计划校验或格式失败后的重试，没有逐字段调用文本模型。

详细汇总：[fixes-measurement-20260926.json](fixes-measurement-20260926.json)。原始计划、候选概率、动作历史和独立验证保存在本机 `artifacts/verification/fixes-20260926/verified/`；浏览器配置及运行缓存不提交到仓库。

## 可视化复核

重启演示服务后，通过 8770 页面重新运行 Copenhagen 酒店任务，6 个动作、2 次 Laya、1 次规划调用，带截图共 3.180 秒。已人工检查截图，并核对页面显示：The Glasshouse、Design、Free cancellation enabled、Destination Copenhagen。界面保留在该详情页，本机轨迹为 `artifacts/verification/fixes-20260926/visible-copenhagen.json`。

## 仍存在的问题

- **Qwen 规划仍会犯语义错误**。中文文章最终运行里，重试后的 open 错误地复制了提示例子的 “Why memory leaks happen”；Laya 使用原始中文请求，在四个观察到的链接和 none 中选择了正确文章，随后独立正文检查通过。正确候选分数为 0.3744，返回 confidence 为 0.0678；这些数值不是任务准确率。该成功不能归功于 Qwen 完全理解了任务。
- **Python 文档**：规划器凭空增加 Language=Python、版本、主题等要求，偏离打开章节的请求。导航读取修复后最终运行能继续，但任务仍失败。
- **Wikipedia**：规划器加入 article title/article body 等虚构字段，随后字段匹配及导航偏离目标。最终没有打开目标文章。
- **Google Flights**：规划混入单程不需要的 Return 日期，以及泛指 Flights 的 open。最终检查中单程、目的地、日期与年份满足，但起点、搜索结果页和航班结果未通过；程序停止等待，没有声称完成。
- 表单关联信息缺失、自动应用筛选、站点无明显结果反馈、长页面滚动和复杂动态控件仍需单独评估。当前是 DOM 浏览器原型，不是通用桌面视觉模型。

## 复现

先启动 `examples/local_demo.py`，再运行：

```bash
uv run --extra local --offline python -m examples.evaluate_local \
  --scenarios zh travel research copenhagen nature article_zh article_en changed_route uncheck_existing \
  --output artifacts/verification/local-regression

uv run --extra local --offline python -m examples.evaluate_local \
  --scenarios python_docs wikipedia flights \
  --output artifacts/verification/public-regression
```

公共网站测试当前会返回非零退出码。不要通过删掉失败案例或只检查 DONE 来让测试变绿。

离线开发门禁：89 项测试；Ruff、两份 JavaScript 语法检查、源码包和 wheel 构建。离线测试使用模型替身，上表另行调用真实本地模型。
