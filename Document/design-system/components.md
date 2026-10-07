# 共享组件

文档职责：记录 `packages/design-system/` 中共享组件的用途、属性、允许的变体、必须覆盖的状态和使用边界。

适用范围：向共享包新增组件、修改组件属性，或在前端页面中使用共享组件时使用；token 取值见 [`tokens.md`](tokens.md)，视觉理由见 [`design-decision.md`](design-decision.md)。

## 使用方式

组件和样式分开引入。样式只引入一次，放在前端的入口文件里：

```ts
import '@causalagent/design-system/styles.css'
```

组件按需引入：

```ts
import { CaButton, CaCard, CaPageHeader } from '@causalagent/design-system'
```

需要全局注册时使用插件：

```ts
import { CaDesignSystem } from '@causalagent/design-system'

createApp(App).use(CaDesignSystem)
```

样式表里包含品牌基础规则（字体、字重、焦点环、减少动态）。这些规则会影响引入它的整个前端，因此每个前端在开始迁移页面时才引入，不要提前全局引入。

## 组件清单

| 组件 | 用途 | 变体 | 必须覆盖的状态 |
| --- | --- | --- | --- |
| `CaButton` | 提交、导航和文字操作 | `primary`、`secondary`、`quiet` | 默认、悬停、按下、焦点、禁用、加载、长文本 |
| `CaCard` | 面板、卡片和列表容器的表面 | `outline`、`muted`、`raised`、`list` | 默认、悬停（由使用方决定） |
| `CaBadge` | 标签和状态标记 | `neutral`、`muted`、`strong` | 默认、长文本 |
| `CaPageHeader` | 页面和区块的标题区 | `sm`、`md`、`lg` | 有描述、无描述、有操作、长标题 |
| `CaTabs` | 同一区域内的视图切换 | 无 | 默认、选中、悬停、焦点、键盘左右和首尾跳转 |
| `CaInput` | 单行和多行文本输入 | 单行、多行 | 默认、悬停、焦点、禁用、只读、提示、错误、必填 |
| `CaEmptyState` | 没有内容时的说明 | `center`、`start` | 只有说明、有标题、有操作、长文本 |
| `CaLoadingState` | 骨架屏和进度线 | `skeleton`、`line` | 骨架、确定进度、不确定进度、减少动态 |
| `CaErrorState` | 出错时的说明 | 无 | 有描述、无描述、带错误码、有重试操作 |

## CaButton

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `variant` | `primary` \| `secondary` \| `quiet` | `primary` | `primary` 是墨色填充，`secondary` 是纸面加细描边，`quiet` 是下划线文字 |
| `size` | `sm` \| `md` \| `lg` | `md` | 字号和内外边距一起变化 |
| `type` | `button` \| `submit` \| `reset` | `button` | 渲染成按钮时生效 |
| `href` | `string` | 无 | 传入时渲染成链接，此时不渲染 `type` |
| `disabled` | `boolean` | `false` | 灰底加内描边，不接受点击 |
| `loading` | `boolean` | `false` | 标签保留宽度但不可见，原位显示两像素推进线，不接受点击，并声明 `aria-busy` |

插槽：默认插槽是标签文字，`icon` 插槽放在标签之前。

加载态不使用转圈指示器，这条规则来自视觉方向。推进线是两像素的墨线，在减少动态时保留为静止线段，便于仍然看出这是进行中的状态。

使用场景：提交表单、触发分析任务、在卡片和页面标题区放置操作。

不适合：需要表达“删除”或“不可恢复”的操作。危险操作的视觉区分还没有确定，见 [`design-decision.md`](design-decision.md) 的未确定项目；在此之前不要用 `primary` 冒充危险样式，也不要给按钮传入自定义颜色。

## CaCard

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `variant` | `outline` \| `muted` \| `raised` \| `list` | `outline` | `outline` 是纸面加细描边，`muted` 是浅灰底，`raised` 是纸面加面板投影，`list` 是列表容器（28px 圆角加列表投影） |
| `padding` | `none` \| `xs` \| `sm` \| `md` \| `lg` | `md` | 分别是 0、8、16、24、40 像素 |
| `clip` | `boolean` | `false` | 裁掉超出圆角的内容，用于头部和主体共用外边线的卡片；开启后靠边缘的焦点环会被裁掉 |
| `as` | `div` \| `section` \| `article` \| `aside` \| `li` | `div` | 渲染的元素 |

`list` 变体自带 28px 圆角和列表投影，内部通常使用 `padding="xs"`，让每一行自己带内边距。

使用场景：承载一组信息、包裹图表和表格、作为列表容器。

不适合：把卡片当作整体布局容器去嵌套多层卡片。页面分区优先使用间距和分隔线。

## CaBadge

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `tone` | `neutral` \| `muted` \| `strong` | `neutral` | `neutral` 是细描边加次要文字，`muted` 是浅灰底加正文色，`strong` 是墨色反色 |

组件本身不理解业务状态，页面负责把状态映射到色调。共享的状态映射如下，各端不得自行改变含义：

| 业务状态 | 色调 | 说明 |
| --- | --- | --- |
| 待处理、草稿、初始 | `neutral` | 细描边标记 |
| 进行中、运行中、已启用 | `muted` | 浅灰底标记 |
| 成功、已完成 | `muted` | 与进行中一样使用浅灰底，完成与否由文案表达 |
| 失败、已停用、不可用 | `strong` | 墨色反色标记 |
| 提示、提醒、需要注意 | `neutral` | 需要更强提醒时使用通告条，不要给标记加颜色 |

## CaPageHeader

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `title` | `string` | 必填 | 标题文字 |
| `description` | `string` | 无 | 标题下方的说明，字号跟随 `size` |
| `level` | `1` 到 `6` | `2` | 渲染的标题级别，只影响语义，不影响外观 |
| `size` | `sm` \| `md` \| `lg` | `md` | 分别是 18px、36px、48px 的标题 |
| `divider` | `boolean` | `false` | 在标题区下方加一条分隔线 |

插槽：默认插槽放在标题同一行的标题之后，用于放状态标记；`actions` 插槽靠右，用于放操作按钮。

使用场景：页面顶部和区块顶部的标题区。同一页面只使用一个一级标题，其余标题区降级。

## CaTabs

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `modelValue` | `string` | 必填 | 当前选中的标签 id |
| `items` | `{ id: string; label: string }[]` | 必填 | 标签列表 |
| `ariaLabel` | `string` | 无 | 标签组名称，必须提供可读的名称 |

事件：`update:modelValue`。组件是受控的，自己不改选中的标签。

插槽：`panel` 使用方提供面板内容，作用域参数是标签 id；不提供时组件只渲染标签栏。

键盘：左右方向键在标签之间移动并同时切换，Home 和 End 跳到第一个和最后一个。组件自己管理焦点位置和 `aria-controls` 关联。

使用场景：同一区域内切换视图，例如因果图、指标表和报告。

不适合：页面级导航，也不会根据地址变化切换内容。地址相关的切换仍由各前端自己的路由和导航处理。

## CaInput

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `modelValue` | `string` \| `number` | `''` | 输入内容 |
| `label` | `string` | 无 | 标签，与控件通过 `for` 绑定 |
| `hint` | `string` | 无 | 提示文字 |
| `error` | `string` | 无 | 错误文字，出现时声明 `aria-invalid` 并进入无障碍描述 |
| `multiline` | `boolean` | `false` | 渲染成文本域 |
| `rows` | `number` | `4` | 文本域行数 |
| `disabled` / `readonly` / `required` | `boolean` | `false` | 直接透传到原生控件 |

事件：`update:modelValue`、`focus`、`blur`。

错误文字沿用原型的失败态表达：正文色加下划线，不引入彩色。

使用场景：表单字段、筛选条件和报告参数。

不适合：把多行文本域当作富文本编辑器；报告正文和 Markdown 输入仍由各端自己的组件负责。

## CaEmptyState

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `title` | `string` | 无 | 标题 |
| `description` | `string` | 无 | 说明文字 |
| `align` | `center` \| `start` | `center` | `start` 用于还原原型里左对齐的一行空态文字 |

插槽：`icon`、默认插槽（放在说明之后）、`actions`。

使用场景：列表为空、还没有任务、筛选没有结果。

不适合：把出错当作空状态。请求失败必须使用 `CaErrorState`，否则用户无法区分“没有数据”和“没有取到数据”。

## CaLoadingState

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `variant` | `skeleton` \| `line` | `skeleton` | `skeleton` 是三行骨架，`line` 是两像素进度线 |
| `lines` | `number` | `3` | 骨架行数，最后一行是短行 |
| `showTitle` | `boolean` | `true` | 骨架是否带一行较宽的标题占位 |
| `value` | `number` | 无 | 0 到 1 的进度；不传时进度线来回推进，表示进度未知 |
| `label` | `string` | `加载中` | 给读屏软件的说明文字 |

确定进度时暴露 `progressbar` 角色和进度值，不确定进度和骨架暴露 `status` 角色并声明忙碌。

使用场景：面板首次加载、列表刷新、已知总量的推进过程。

不适合：用骨架长期占位真实的空状态；加载结束后必须换成真实内容、空状态或错误状态。

## CaErrorState

| 属性 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `title` | `string` | 必填 | 出错的说明标题 |
| `description` | `string` | 无 | 下一步怎么做 |
| `code` | `string` | 无 | 错误码或 request ID，使用等宽字体展示 |

插槽：`actions` 用于放重试按钮。

组件使用 `role="alert"`，出现时会通知读屏软件。

使用场景：请求失败、服务不可用、任务失败。

不适合：表单字段级的校验提示。字段级提示使用 `CaInput` 的 `error`。

## 图标与插槽约定

共享包不引入图标依赖。需要图标的组件都通过插槽接收使用方自己的图标组件，图标按 [`design-decision.md`](design-decision.md) 的线性画法使用，尺寸由使用方控制。

## 下一批候选组件

以下模式在官网原型里已经出现，但本轮不做成共享组件，避免把官网的页面结构带进应用端：

```text
CaSegmented   原型 .seg，分段的单选控件，用于图表指标切换
CaTable       原型 .mini-table 和 .compare-table，应用端各自已有的表格
CaToolbar     原型 .compare-tools 和 .runbar，操作与进度同排
CaNotice      原型 .notice，通告条
CaSessionList 原型 .sessions，会话列表
```

迁移应用页面时如果确认某个模式会是多端共用的，再按同一套流程加入共享包，并同步更新本文件。
