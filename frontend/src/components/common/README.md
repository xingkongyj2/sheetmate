# 确认弹窗

`ConfirmDialog.vue` 在 `App.vue` 中挂载一次。业务组件在 `setup` 中调用 `useConfirm()`，通过同一套弹窗发起删除或其他确认操作。

```ts
const { confirmDelete, confirm } = useConfirm()

async function removeFile(file: FileInfo) {
  const deleted = await confirmDelete({
    title: '删除这个文件？',
    target: file.filename,
    description: '文件将被永久删除，之后无法再预览或下载。',
    confirmText: '删除文件',
    action: () => fileApi.remove(file.file_id),
  })
  if (deleted) await refreshFiles()
}

async function closeEditor() {
  if (await confirm({
    title: '放弃未保存的修改？',
    description: '关闭后，本次尚未保存的修改将丢失。',
    confirmText: '放弃修改',
    cancelText: '继续编辑',
  })) emit('close')
}
```

- `target` 独立展示对象名称，按纯文本渲染，长名称自动换行。
- `action` 只放实际提交操作；成功后返回 `true`，列表刷新和导航放在调用方处理。
- 执行期间禁用重复提交和关闭；失败时保留弹窗并显示错误，允许重试。
- 取消按钮、关闭按钮、Esc 和点击遮罩返回 `false`。初始焦点在取消按钮，Tab 保持在弹窗内。
- 调用组件卸载时取消未完成的确认；已经发出的请求不会被撤销。已有确认窗口时忽略重复请求。
- `confirmDelete` 使用红色删除样式；`confirm` 使用提醒图标和黑色主按钮。

新增确认入口请复用此模块，不再调用 `window.confirm`。
