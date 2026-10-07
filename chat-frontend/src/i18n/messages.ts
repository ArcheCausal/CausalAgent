import type { Locale } from '../types/domain'

export const messages = {
  zh: {
    login: '登录', register: '注册', username: '用户名', password: '密码', confirmPassword: '确认密码',
    loginHint: '还没有账号？点击注册', registerHint: '已有账号？点击登录',
    showPassword: '显示密码', hidePassword: '隐藏密码',
    newChat: '新建对话', sessions: '会话', fileList: '文件列表', settings: '设置', userInfo: '用户信息',
    collapseSidebar: '收起侧边栏', expandSidebar: '展开侧边栏', sidebarNavigation: '侧边栏快捷导航',
    adminPortal: '管理后台', ragEvalPortal: 'RAG 评测台', logout: '退出登录', close: '关闭', cancel: '取消', back: '返回',
    userAgreement: '用户协议', userManual: '操作文档', checkUpdate: '检查更新', toggleLanguage: '切换英文',
    welcomeTitle: '上传因果数据文件，开始因果分析',
    welcomeDescription: '我会基于你上传的数据进行分析并生成因果关系结果。',
    inputPlaceholder: '输入消息...', waitingPlaceholder: '请输入恢复任务的回答...',
    upload: '上传', uploading: '上传中...', send: '发送', cancelJob: '取消任务', canceling: '取消中...',
    webSearch: '联网搜索', webSearchOn: '联网搜索：开', webSearchOff: '联网搜索：关',
    noHistory: '还没有任何对话记录。', noFiles: '还没有任何文件记录。',
    pleaseLogin: '请先登录', loading: '加载中...', thinking: '正在处理', processed: '已处理', inProgress: '进行中',
    waitingInput: '等待补充输入', taskFailed: '任务执行失败', taskCanceled: '任务已取消',
    noPermission: '无管理员权限', registerSuccess: '注册成功！请登录。',
    lastLoginWarning: '登录成功，但最后登录时间写入失败',
    versionUpdated: '版本已经更新到最新', userAgreementTitle: '用户协议', userManualTitle: '操作文档',
    deleteSessionConfirm: '确定要永久删除此会话及其所有消息吗？此操作无法撤销。',
    deleteFileConfirm: '确定要永久删除此文件吗？此操作无法撤销。', delete: '删除',
    selectFile: '选择文件', clearFile: '清除文件选择', accountPrefix: '账号：',
  },
  en: {
    login: 'Login', register: 'Register', username: 'Username', password: 'Password', confirmPassword: 'Confirm Password',
    loginHint: "Don't have an account? Register", registerHint: 'Already have an account? Login',
    showPassword: 'Show password', hidePassword: 'Hide password',
    newChat: 'New Chat', sessions: 'Conversations', fileList: 'File List', settings: 'Settings', userInfo: 'User Info',
    collapseSidebar: 'Collapse sidebar', expandSidebar: 'Expand sidebar', sidebarNavigation: 'Sidebar shortcuts',
    adminPortal: 'Admin Console', ragEvalPortal: 'RAG Lab', logout: 'Logout', close: 'Close', cancel: 'Cancel', back: 'Back',
    userAgreement: 'User Agreement', userManual: 'User Manual', checkUpdate: 'Check Update', toggleLanguage: 'Switch to Chinese',
    welcomeTitle: 'Upload a causal data file to start causal analysis',
    welcomeDescription: 'I will analyze your uploaded data and generate causal relationship results.',
    inputPlaceholder: 'Type a message...', waitingPlaceholder: 'Enter an answer to resume the task...',
    upload: 'Upload', uploading: 'Uploading...', send: 'Send', cancelJob: 'Cancel task', canceling: 'Canceling...',
    webSearch: 'Web Search', webSearchOn: 'Web Search: On', webSearchOff: 'Web Search: Off',
    noHistory: 'No conversation history yet.', noFiles: 'No files yet.',
    pleaseLogin: 'Please login', loading: 'Loading...', thinking: 'Processing', processed: 'Processed', inProgress: 'In progress',
    waitingInput: 'Waiting for input', taskFailed: 'Task failed', taskCanceled: 'Task canceled',
    noPermission: 'No administrator permission', registerSuccess: 'Registration succeeded. Please login.',
    lastLoginWarning: 'Login succeeded, but the last login time could not be recorded.',
    versionUpdated: 'Version is up to date', userAgreementTitle: 'User Agreement', userManualTitle: 'User Manual',
    deleteSessionConfirm: 'Delete this conversation and all messages permanently? This cannot be undone.',
    deleteFileConfirm: 'Delete this file permanently? This cannot be undone.', delete: 'Delete',
    selectFile: 'Select file', clearFile: 'Clear file selection', accountPrefix: 'Account: ',
  },
} as const

export type MessageKey = keyof typeof messages.zh
export type Messages = Record<MessageKey, string>
export function localeMessages(locale: Locale): Messages {
  return messages[locale]
}
