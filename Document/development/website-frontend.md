# 官网 Vue 前端

文档职责：记录官网 Vue 3 工程的页面清单、原型移植方式、认证页面与回跳规则、构建、开发与验收边界。

适用范围：`website-frontend/`、Flask 的官网页面入口和登录注册页面；不覆盖普通用户应用、RAG 评测台、管理员前端或后端认证实现。

## 入口与页面

`app/main/routes.py` 的 `main_bp` 提供全部官网页面：`/`、`/product`、`/pricing`、`/about`、`/docs`、`/changelog` 以及 `/auth/sign-in`、`/auth/sign-up`。这些路径都返回同一份构建产物，页面由浏览器按 `location.pathname` 选择（`src/routes.ts`）；未登记的路径不由 `main_bp` 提供，访问得到 404，构建产物内的 `not-found` 页面只作为兜底。`/dashboard`、`/rag-eval`、`/admin` 只作为真实系统入口链接出现在导航和页脚，不由官网产物提供。

每个公开地址都是整页加载，`src/App.vue` 按 `location.pathname` 异步引入对应页面组件，因此同一个文档只会解析一个页面。页面的标题和说明按地址取自 `src/routes.ts` 的 `SITE_PAGE_META`。

登录后的普通用户应用在 `/dashboard`，RAG 评测台在 `/rag-eval`，管理员系统在 `/admin`，都不属于本工程。

## 认证页面与回跳

登录页面调用 `POST /api/login` 并原样回传查询参数里的 `next`；注册页面调用 `POST /api/register`，成功后提示去登录。服务端只接受白名单内的站内路径作为回跳目标，登录成功后返回 `redirect_to`：管理员默认 `/admin/database`，普通用户默认 `/dashboard`。

客户端在跳转前还会用 `src/api/auth.ts` 的 `isAllowedInternalPath` 再校验一次 `redirect_to` 与 `next`，只接受 `/dashboard*`、`/admin*` 和 `/rag-eval`，其余回落到 `/dashboard`。服务端仍是权威判定，客户端校验只用于避免把浏览器送到未登记地址。

`?notice=admin_required` 会在登录页显示“当前账号没有管理员权限”的提示。认证页的字段校验、密码显隐、密码规则与提交状态取自原型脚本，提交动作改为调用真实接口，并在成功后按白名单回跳。

## 内容组织

六个公开页面与两个认证页面直接沿用官网原型
（`杂项/前端/官网原型精细化规划/`，仓库外的设计源）的结构、样式与交互：

- `src/pages/` 下每个页面组件承载原型对应页面的完整标记；
- `src/styles/pages/<page>.css` 由原型该页的内联样式逐字转写，认证页共用 `src/styles/pages/auth.css`；
- `src/scripts/<page>.js` 由原型该页的内联脚本逐字转写，导出初始化函数，页面挂载后调用；认证页共用 `src/scripts/causal-map.js`。

原型的页面脚本以 `window.gsap` 与 `window.ScrollTrigger` 使用 GSAP，但原型目录导出的 vendor 文件缺失。官网以依赖 `gsap@3.15.0` 提供这两个库，首页与更新日志页挂载时调用 `src/scripts/vendor-gsap.js` 按原型的全局约定把它们挂到 `window`，页面脚本保持逐字不改；其余页面不引入 GSAP。GSAP 与滚动触发动效留在官网，不进入共享包。

页面只引用已登记路径或真实系统入口。通告条文本、关闭行为、章节索引、滚动进度、Canvas 因果图、滚动触发渐入与认证页的三维因果图都来自原型脚本。`/docs` 面向使用者，`/changelog` 只记录影响使用方式的变化；仓库内部的 `Document/` 与 `CHANGELOG.md` 不通过官网发布。首页、产品页和认证页使用的标志、专家照片、论文页面与基准图是发布到 `public/assets/` 与 `public/` 的静态资源，通过 `/site-assets/` 同源提供；官网不在运行时加载普通用户应用、RAG 评测台或管理员前端。

页头与认证页的品牌标志使用发布在 `public/brand/causalagent-mark.svg` 的仓库品牌图，不再绘制文字字形。首页“隐私、保护与RAG管理”轮播和产品页的配图位置使用登录后采集的真实界面截图：工作台 `public/media/workspace-console.png`、管理员端 `public/media/admin-console.png`、RAG 测评台 `public/media/rag-eval-console.png` 与日志仪表盘 `public/media/observability-logs.png`。四张图统一为 1600×1000（16:10），页面以 `object-fit: cover` 与顶部对齐裁切显示；截图只包含聚合指标和状态，不含知识源清单、内容哈希或用户文件内容。

原型自带的定制中文字体不在约定范围内，页面统一使用共享设计系统的 `--ca-font-sans`，字形度量由此产生的细微差异属于允许保留的差异；滚动交接、Canvas 因果图、滚动触发渐入与 GSAP 相关逻辑留在官网，不进入共享包。

## 构建与部署

构建产物由 Flask 在同源路径提供：页面入口是 `main_bp` 的官网路径，静态资源使用 `/site-assets/<path:filename>`。构建目录默认是 `website-frontend/dist/`，Docker 运行时使用 `/opt/causalagent-website`，也可由 `WEBSITE_FRONTEND_DIST_DIR` 指定；目录缺少 `index.html` 时页面入口和资源路径统一返回带 request ID 的 503 和 `website_frontend_missing`。Vite base 是 `/site-assets/`，入口 HTML 不缓存，`assets/` 下的带 hash 资源使用长期 `public, immutable` 缓存。

## 开发与测试

```powershell
Push-Location website-frontend
npm ci
npm run dev
Pop-Location
```

开发服务器使用 5175 端口并把 `/api` 代理到 `http://127.0.0.1:5001`；依赖使用 `.npmrc` 固定的 `legacy-peer-deps`，安装请用 `npm ci`。`npm run check` 依次执行类型检查、Vitest 单元测试和生产构建，单元测试覆盖路由解析、页面元信息、登录回跳白名单、认证客户端和登录表单交互，以及页面引用的发布素材与站内链接。单独执行时使用 `npm run typecheck`、`npm run test:unit` 和 `npm run build`。

构建产物中 `HomePage` 的样式表约 88 KB、其余页面样式表 15 到 30 KB，认证页样式表约 15 KB；`gsap` 进入入口分块，页面脚本按需引用。

## 验收边界

自动化检查只证明路由解析、客户端校验、表单交互和构建成功，不证明真实 Flask、Cookie Session、MySQL、worker、模型或浏览器人工验收。发布前需要人工确认：官网各页面可直接访问和刷新、`/auth/sign-in` 与 `/auth/sign-up` 可刷新、登录后按角色回到 `/dashboard`、`/rag-eval` 或 `/admin/database`、外部 `next` 被忽略、未登录访问 `/dashboard` 会回到登录页。

