# 设计 token

文档职责：记录共享设计系统中 token 的命名规则、数值、与官网原型变量的对应关系，以及页面和组件使用 token 的规则。

适用范围：修改 `packages/design-system/src/styles/tokens/` 或任何前端的颜色、字体、间距、圆角、投影和动效取值时使用；视觉决策的理由见 [`design-decision.md`](design-decision.md)。

## 命名与分层

token 统一使用 `--ca-` 前缀，避免与四个前端已有的变量名冲突。分两层使用：

第一层是基础取值，包括灰阶、字号、间距、圆角、投影和动效时长，名称描述取值本身，例如 `--ca-ink`、`--ca-space-16`、`--ca-radius-card`。

第二层是语义取值，描述用途，例如 `--ca-surface-panel`、`--ca-text-muted`、`--ca-border-default`。页面和组件只使用第二层。

token 声明在 `@layer ca-tokens` 里。业务页面里没有放进层级的变量声明优先级更高，因此某个前端需要整体换一套取值时，可以在自己的样式里覆盖同名变量，不需要改共享包。

## 颜色

| token | 取值 | 原型变量 | 用途 |
| --- | --- | --- | --- |
| `--ca-paper` | `#ffffff` | `--color-paper` | 纸白，页面和卡片的底色 |
| `--ca-vellum` | `#f3f3f3` | `--color-vellum` | 浅灰底，用于弱化分区和选中行 |
| `--ca-hairline` | `#e5e7eb` | `--color-graphite-hairline` | 默认描边和分隔线 |
| `--ca-stone` | `#c7c7c7` | `--color-stone` | 较强的描边、装饰性线段 |
| `--ca-ash` | `#a0a0a0` | `--color-ash` | 只用于占位文字和禁用态 |
| `--ca-slate` | `#6f6f6f` | `--color-slate` | 白底上的次要文字 |
| `--ca-pewter` | `#5e5e5e` | `--color-pewter` | 白底上需要强调的次要文字 |
| `--ca-carbon` | `#222222` | `--color-carbon` | 图标默认颜色 |
| `--ca-ink` | `#171717` | `--color-ink` | 正文和主操作的墨色 |
| `--ca-ink-hover` | `#000000` | `--color-ink-hover` | 墨色的悬停加深 |
| `--ca-notice` | `#ffe9bf` | `--color-cream-notice` | 通告条底色，全站唯一的彩色 |
| `--ca-hairline-inverse` | `rgb(255 255 255 / 22%)` | `rgba(255, 255, 255, 0.18)` | 反色面上的分隔线 |

语义颜色：

| token | 取值 | 用途 |
| --- | --- | --- |
| `--ca-surface-page` | `--ca-paper` | 页面底色 |
| `--ca-surface-panel` | `--ca-paper` | 面板和输入控件底色 |
| `--ca-surface-muted` | `--ca-vellum` | 弱化分区、选中行和禁用底色 |
| `--ca-surface-inverse` | `--ca-ink` | 反色区块底色 |
| `--ca-surface-notice` | `--ca-notice` | 通告条底色 |
| `--ca-text-default` | `--ca-ink` | 正文和标题 |
| `--ca-text-muted` | `--ca-slate` | 白底上的次要文字 |
| `--ca-text-muted-strong` | `--ca-pewter` | 白底上需要强调的次要文字 |
| `--ca-text-subtle` | `--ca-ash` | 占位文字和禁用态专用 |
| `--ca-text-inverse` | `--ca-paper` | 反色面上的文字 |
| `--ca-text-inverse-muted` | `--ca-stone` | 反色面上的次要文字 |
| `--ca-text-inverse-subtle` | `--ca-ash` | 反色面上更次要的说明 |
| `--ca-border-default` | `--ca-hairline` | 默认描边 |
| `--ca-border-strong` | `--ca-stone` | 需要更清楚的描边 |
| `--ca-border-inverse` | `--ca-hairline-inverse` | 反色面上的分隔线 |
| `--ca-focus-ring` | `--ca-ink` | 默认焦点环 |
| `--ca-focus-ring-inverse` | `--ca-paper` | 反色面上的焦点环 |

图表颜色 `--ca-chart-ink`、`--ca-chart-pewter`、`--ca-chart-slate`、`--ca-chart-stone`、`--ca-chart-grid` 和 `--ca-chart-wash` 是灰阶的别名，用于图表分层，不使用彩色。

## 字体

| token | 取值 |
| --- | --- |
| `--ca-font-sans` | 随包 Geist Sans 400（拉丁字形）、Noto Sans SC 400（中文和 CJK 字形），最后回退到系统无衬线 |
| `--ca-font-mono` | 系统等宽字体栈，用于代码、SQL 摘要和错误码 |
| `--ca-weight-regular` | `400` |
| `--ca-text-caption` / `--ca-leading-caption` | `12px` / `1.5` |
| `--ca-text-body-sm` / `--ca-leading-body-sm` | `14px` / `1.43` |
| `--ca-text-body` / `--ca-leading-body` | `16px` / `1.5` |
| `--ca-text-subheading` / `--ca-leading-subheading` | `18px` / `1.38` |
| `--ca-text-lede` / `--ca-leading-lede` | `20px` / `1.6` |
| `--ca-text-heading-sm` / `--ca-leading-heading-sm` | `36px` / `1.25` |
| `--ca-text-heading` / `--ca-leading-heading` | `48px` / `1.11` |
| `--ca-text-heading-lg` / `--ca-leading-heading-lg` | `60px` / `1.1` |
| `--ca-text-display` / `--ca-leading-display` | `72px` / `1.2` |

`--ca-font-sans` 是跨前端统一的品牌字体栈。Geist Sans 的 unicode-range 必须匹配实际字体文件并排除 CJK 标点；中文、CJK 标点和全角字符由随包的 Noto Sans SC WOFF2 子集绘制。当前 vendored 的 Noto Sans SC 400 子集来自 @fontsource/noto-sans-sc 5.3.0。页面不得依赖 Windows、macOS 或 Linux 是否安装中文字体。当前只提供 400 字重，字体授权文件保存在 `packages/design-system/src/fonts/licenses/`，构建后以内容摘要文件名复制到前端产物的 `assets/font-licenses/`，发布字体资源时必须一并保留授权文件。

中文正文使用中文全角标点（，。！？：；（））。半角 ASCII 标点仍按英文宽度排版，字体不会改写正文内容。

`--ca-font-mono` 是应用端补充的取值，官网原型页面没有代码区块，因此原型里没有对应变量。

## 间距与版式

| token | 取值 | 用途 |
| --- | --- | --- |
| `--ca-space-4` 到 `--ca-space-160` | 4、8、12、16、24、32、40、48、56、64、80、112、128、160px | 间距量表，只用这些取值 |
| `--ca-page-max-width` | `1200px` | 页面内容最大宽度 |
| `--ca-section-gap` | `80px` | 区块之间的大间距 |
| `--ca-card-padding` | `24px` | 卡片默认内边距 |
| `--ca-element-gap` | `8px` | 元素之间的默认间距 |
| `--ca-nav-height` | `72px` | 顶部导航高度 |
| `--ca-rail-width` | `264px` | 应用端侧栏宽度，取自原型分析台的会话栏 |
| `--ca-measure-narrow` | `46ch` | 简短说明和空态文案的阅读宽度 |
| `--ca-measure-body` | `52ch` | 卡片正文的阅读宽度 |
| `--ca-measure-wide` | `62ch` | 大段说明的阅读宽度 |
| `--ca-measure-lede` | `720px` | 区块导语的阅读宽度 |

元素的间距由容器使用 `gap` 提供，不用外边距堆叠。这一条与官网原型一致。

## 圆角、描边与投影

| token | 取值 | 原型变量 |
| --- | --- | --- |
| `--ca-radius-small` | `8px` | `--radius-small-cards` |
| `--ca-radius-card` | `20px` | `--radius-cards` |
| `--ca-radius-large` | `24px` | `--radius-large-cards` |
| `--ca-radius-list` | `28px` | `--radius-list-items` |
| `--ca-radius-full` | `9999px` | `--radius-full` |
| `--ca-border-width` | `1px` | 原型中散落的 `1px` 描边 |

| token | 原型变量 | 用途 |
| --- | --- | --- |
| `--ca-shadow-raise` | `--shadow-xl` | 悬停时的小幅抬升 |
| `--ca-shadow-panel` | `--shadow-xl-3` | 单个面板和任务台 |
| `--ca-shadow-list` | `--shadow-xl-2` | 列表式容器 |

## 动效

| token | 取值 | 用途 |
| --- | --- | --- |
| `--ca-motion-fast` | `200ms` | 颜色、背景等小交互 |
| `--ca-motion-hover` | `240ms` | 悬停引起的投影变化 |
| `--ca-motion-enter` | `320ms` | 入场 |
| `--ca-motion-exit` | `210ms` | 退出 |
| `--ca-motion-progress` | `1400ms` | 进度线的线性推进 |
| `--ca-ease-enter` | `cubic-bezier(0.16, 1, 0.3, 1)` | 入场缓动 |
| `--ca-ease-out` | `cubic-bezier(0.2, 0.8, 0.2, 1)` | 一般过渡缓动 |

## 使用规则

- 页面和组件只使用语义 token，不直接写颜色字面值、圆角数值和投影。
- 需要新的颜色或圆角时，先判断它是否属于已有语义；属于新语义时必须先更新本文件，再改共享包。
- 单个页面需要覆盖共享取值时，覆盖语义 token，不要覆盖组件内部样式。
- 组件样式类都以 `ca-` 开头，业务页面不要复用这些类名，也不要往共享组件里传入颜色、圆角或投影。
