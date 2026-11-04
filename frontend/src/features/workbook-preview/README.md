# 原始工作簿预览

`WorkbookPreview.vue` 是文件预览的统一入口，接收已上传文件的 `file: FileInfo`，或首页待发送附件的 `localFile: File`，关闭时发出 `close`。本地 XLSX/XLSM/CSV 直接读取文件内容；旧 XLS 使用鉴权临时解析接口，所有预览均不创建会话或文件记录。

开启 `insertable` 后，预览顶部显示工作表名称按钮；点击发出 `selectTable({ filename, sheet })`。父组件负责把引用插入输入框保存的光标位置，并按需要关闭预览。默认不显示引用操作，原有只读查看入口不受影响。

- XLSX / XLSM：原始文件经现有鉴权下载接口获取，在 Web Worker 中由 ExcelJS 解析并映射为 Univer 快照。
- CSV：在同一 Worker 中解码、识别分隔符，按文本值显示，保留前导零和原始坐标。
- 旧 XLS：已上传文件使用原有分页数据接口；未上传的本地 XLS 使用 `POST /api/files/preview-upload` 临时解析。均在同一弹窗中分页展示，界面明确提示不保留排版；本地 XLS 按需随每页请求提交到后端临时目录，解析后删除，限制 25 MB 且不超过系统上传限制。
- `viewer.ts`：只读权限、中文界面、原生 Sheet 标签和缩放；隐藏编辑工具和菜单。
- `excel/`：与页面无关的 ExcelJS → Univer 转换模块。
- `worker-client.ts` / `parse.worker.ts`：隔离解析任务，关闭弹窗时终止，超时和异常可重试。

文件不会发送至外部服务，后端不新增转换服务。公式使用文件内缓存结果，复杂对象和缺少缓存的公式通过显示提示说明。原件始终不被修改。

依赖采用同一固定版本的 Univer 开源核心，无 Pro 导入服务。`WorkbookPreview` 与 Univer 渲染器按需加载，不进入聊天首屏主包。

对话中的原始输入、期望结果和正式输出使用文件卡片，提供查看和下载，不重复展示解析后的数据表。执行明细中的标准表格也打开关联工作簿。仅临时查询和中间计算表可以内联：最多 20 行、8 列、120 个单元格，且必须包含完整数据；已导出为文件的表不重复内联。展示规则位于 `composables/tablePresentation.ts`，文件关联和去重位于 `composables/filePresentation.ts`。
