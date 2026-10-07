# packages/design-system/AGENTS.md

生效目录：`packages/design-system/` 及其子目录。

负责约束的修改类型：设计 token、品牌基础样式、共享组件的源码与样式、本包的类型检查和单元测试。

## 修改前必须阅读

- 必须阅读 [`../../Document/design-system/design-decision.md`](../../Document/design-system/design-decision.md)、[`../../Document/design-system/tokens.md`](../../Document/design-system/tokens.md) 和 [`../../Document/design-system/components.md`](../../Document/design-system/components.md)。
- 必须检查 `src/styles/tokens/` 中是否已经存在同类取值，`src/components/` 中是否已经存在同类组件，以及 `tests/` 中已有的行为断言。
- 修改颜色、字体、圆角、投影或组件属性时，必须核对四个前端当前是否有同名样式或同名组件，避免引入冲突的语义。

## 取值与样式规则

- 颜色字面值只允许出现在 `src/styles/tokens/colors.css`；其他文件和业务页面一律使用语义 token。
- 页面和组件只使用语义 token，不直接使用基础灰阶变量。
- 不得为单个组件新增 token。确实需要新语义时，先更新 `Document/design-system/tokens.md`，再加变量。
- 组件不得开放传入任意颜色、圆角或投影的属性，也不得接受使用方自定义样式类来改变外观。
- 类名统一使用 `ca-` 前缀；组件样式不得依赖业务页面的类名。
- 不引入图标库和其他新的运行时依赖；`vue` 是 peer 依赖，只在开发依赖中固定版本供本包自检使用。

## 无障碍与状态

- 必须保留组件现有的角色声明和属性关联：标签页的 `tablist` 与漫游焦点、输入控件的 `aria-describedby` 与 `aria-invalid`、加载状态的忙碌与进度声明、错误状态的 `role="alert"`。
- 必须保留禁用、加载、空、错误和长文本状态；新增状态时同步补充测试和 `Document/design-system/components.md`。
- 必须保留 `prefers-reduced-motion` 下的静态终态，不得让加载或进度信息只依赖动画表达。

## 验证义务

- 修改后至少运行 `npm ci`、`npm run typecheck`、`npm run test:unit`，并确认 `npm run check` 通过。
- 涉及视觉结果、字体或布局的改动，必须说明尚未在哪个前端页面和哪次浏览器核对中验证；本包的自检不构成视觉验收。
- 接入某个前端时，还必须在该前端运行它的类型检查和构建，并在真实页面上核对桌面端和移动端表现。
