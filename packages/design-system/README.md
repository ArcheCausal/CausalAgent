# @causalagent/design-system

文档职责：说明本包的目录结构、引入方式、开发命令和改动约束，供在本包内工作或把本包接入某个前端的开发者阅读。

适用范围：`packages/design-system/`；视觉决策、token 清单和组件契约的权威说明在 `Document/design-system/`，本文件不重复那些内容。

## 内容

```text
src/
  index.ts                 组件与类型的统一导出，以及可选的全局注册插件
  types.ts                 组件对外暴露的类型
  fonts/                   Geist Sans 与 Noto Sans SC 的 WOFF2 子集及 OFL 授权文件
  styles/
    index.css              唯一推荐的样式入口，按字体、token、基础、组件的顺序引入
    fonts.css              品牌字体声明及字符范围分配
    noto-sans-sc-400.css   Noto Sans SC 400 的 Unicode 子集声明
    base.css               品牌基础规则：字体、字重、焦点环、文字链接、减少动态
    tokens/                设计 token，分颜色、语义、字体、间距、形状、投影和动效
    components/            各组件的样式，类名以 ca- 开头
  components/              9 个基础组件的 Vue 单文件组件
tests/                     组件行为、键盘操作和无障碍契约的单元测试
vite/                      Vite 构建钩子，随前端产物发布字体 OFL 授权文本
```

本包以源码形式参与各前端的构建，不单独产出构建产物，也不发布到包仓库。

## 设计事实的归属

视觉方向、颜色对比度、动效边界、token 数值和组件契约都记录在 `Document/design-system/`，修改本包之前先读对应页面：

- `Document/design-system/design-decision.md`
- `Document/design-system/tokens.md`
- `Document/design-system/components.md`

新增 token、修改颜色取值或改变组件属性时，文档和源码必须在同一次改动里一起更新。

## 引入方式

样式只引入一次，放在前端入口文件里：

```ts
import '@causalagent/design-system/styles.css'
```

组件按需引入或使用插件全局注册，两种方式的例子见 `Document/design-system/components.md`。

样式表包含品牌基础规则，会影响引入它的整个前端。每个前端在自己开始迁移页面时才引入，不要提前全局引入。

## 接入某个前端

管理员端和 RAG 评测台已接入本包；官网和聊天端完整接入时需要：

1. 在前端的 `vite.config.ts` 里把 `@causalagent/design-system` 指向本目录的 `src`，让源码参与该前端的构建；
2. 在前端的 TypeScript 配置里加同样的路径映射，让类型检查能解析到源码；
3. 在前端入口引入完整样式，并确认样式表的路径在本前端可解析，Vite 会把实际使用到的字体子集作为资源一起打包。
4. 在 Vite 配置中启用本目录的字体许可插件，使两个 OFL 文本随带摘要的字体资源一起进入构建产物。

管理员端、聊天端和 RAG 评测台使用 `/` 开头的资源前缀，接入时要同时确认资源前缀和字体文件的相对路径没有问题。管理员端额外通过 `src/element-plus.css` 把 Element Plus 复杂控件适配到共享语义 token。

## 开发与自检

```powershell
Push-Location packages/design-system
npm ci
npm run typecheck
npm run test:unit
npm run check
Pop-Location
```

`npm run check` 串起类型检查和单元测试。测试覆盖组件的变体类名、禁用与加载状态、标签页的键盘行为和无障碍关联、输入控件的标签与错误关联，以及空态、加载态和错误态的角色声明。

自检只证明组件的行为契约，不证明视觉结果，也不证明组件在某个前端页面里的表现。视觉核对需要在接入该前端之后，用真实页面和浏览器完成。

安装依赖必须使用 `npm ci`。本目录的 `.npmrc` 固定了 `legacy-peer-deps`，原因是 vitest 4 的浏览器相关 peer 依赖会让 npm 的依赖解析报错，与 `website-frontend/` 采用同一种处理方式。

## 改动约束

- 颜色字面值只允许出现在 `src/styles/tokens/colors.css`，其他文件和业务页面一律引用变量。
- 组件样式类名以 `ca-` 开头，业务页面不要复用这些类名。
- 组件不开放任意颜色、任意圆角或任意投影的属性。
- 不引入图标库；需要图标的组件通过插槽接收使用方自己的图标组件。
- 新增组件或改变组件属性时，同步更新 `Document/design-system/components.md`。
