# 单机沙盒服务部署配置

文档职责：说明本目录的固定发布引用、服务配置文件、密钥挂载和单机网络前提。

适用范围：开发、兼容副本、预发与生产 Compose 中的单实例沙盒控制服务及同机 OpenSandbox，以及独立测试项目的真实验证边界。

四份根 Compose 通过 `services.yml` 复用服务声明。控制服务入口为 `python -m sandbox_controller`，端口为 8081，单实例、单 API 进程；Worker 等待其 `/healthz` 就绪。控制服务等待主 MySQL healthy、`db-bootstrap` 成功以及 OpenSandbox healthy。PostgreSQL checkpoint 和现有主从回退仍由原服务负责，控制服务仅访问主库，写连接池为 4。

| 容器 | 配置与文件 | 使用方式 |
| --- | --- | --- |
| Worker | `CAUSALAGENT_SANDBOX_CONTROLLER_URL`、`CAUSALAGENT_SANDBOX_CONTROLLER_TIMEOUT_SECONDS`、本地并发与容量等待上限 | 仅控制服务 |
| Worker、controller 与 OpenSandbox 管理服务 | `CAUSALAGENT_SECRET_FILE=/run/secrets/causalagent.env` | 同一份只读 `causalagent_env`，可读取全部应用与数据库密钥 |
| Worker 与 controller | 文件中的 `CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN` | 控制服务认证 |
| controller 与 OpenSandbox 管理服务 | 文件中的 `CAUSALAGENT_SANDBOX_API_KEY` | 上游管理 API 认证 |
| controller | `CAUSALAGENT_SANDBOX_GLOBAL_CAPACITY`、心跳、核查、TTL、镜像、资源及文件限制 | 全局策略，启动时读取 |
| OpenSandbox | `SANDBOX_CONFIG_PATH=/etc/opensandbox/config.toml`、Docker socket、持久状态卷 | 同机 Docker runtime |

`production.env` 保存服务地址、镜像相关配置、容量、超时、资源限制等普通参数，以及 `CAUSALAGENT_SECRET_FILE` 等文件路径；全部应用密钥、数据库密码和两个沙盒认证值由同一份 `causalagent.env` 管理。宿主 `CAUSALAGENT_SECRET_FILE` 选择实际文件，服务器默认为 `/etc/causalagent/secrets/causalagent.env`；四个 Compose 入口均使用 `causalagent_env`，容器内固定读取 `/run/secrets/causalagent.env`。服务器文件沿用 root:3000、0640，配置和密钥均只读挂载。开发默认使用仓库根目录被 Git 忽略的文件，也可通过宿主变量选择仓库外文件。

在 [统一密钥模板](../secrets/causalagent.env.example) 中填写 `CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN` 和 `CAUSALAGENT_SANDBOX_API_KEY`。两个认证值必须不同，均为 32 至 4096 个无空白字符，推荐各生成独立的 32 字节随机十六进制值。所有 Python 组件通过 `config/secret_file.py` 读取同一文件，只要求各自使用的键；controller 从中读取数据库密码，不依赖模型配置。三个独立凭据来源及 token/key 文件路径配置不再使用，不支持旧来源兼容；`controller.env.example` 仅保留统一模板的导航说明，不是发布所需模板。不要把密钥值放进命令参数或普通环境模板。

三个管理侧服务共享全部密钥，不提供它们之间的密钥隔离。执行用户脚本的临时沙盒容器不得挂载 `causalagent.env`，也不得接收应用、数据库或沙盒服务密钥。`server_entrypoint.py` 通过统一加载器读取 `CAUSALAGENT_SANDBOX_API_KEY`，只将其导出为官方 server 支持的 `OPENSANDBOX_SERVER_API_KEY` 进程环境；Compose environment 与进程参数不含密钥值，服务不启用无鉴权模式。

`config.toml` 仍是必须保留的结构化运行配置，不是额外凭据文件。`CAUSALAGENT_OPENSANDBOX_CONFIG_SOURCE` 可指定经过核对的自定义 TOML。发布还会按清单生成 `images.env`，因此统一普通配置与密钥不表示部署总共只有两个文件。`opensandbox_state` 保存上游服务自身的元数据，控制服务生命周期事实仍在现有 MySQL 中。

截至 2026-10-25，使用正式版 `release-1.1.1`，其发布时间为 2026-10-09。`1.2.0-rc.2` 是候选版；发布列表中某些 RC 的 `prerelease` 标志并不准确，不能只依赖 GitHub 的 latest 标志。固定值、官方来源与核实方式见 [release-lock.json](release-lock.json)。server、execd、egress 的 digest 与官方 BOM 和实际 registry manifest 一致；Code Interpreter 基础镜像采用官方 SDK 示例的 `v1.1.0` 并固定 digest。

数据处理镜像从本目录 Dockerfile 构建，使用现有 hash 锁定的依赖；目标平台为 Linux amd64。官方 Python 由 uv 管理，处理依赖安装到 `/opt/causalagent/venv`，继承基础镜像的系统包；本目录的启动适配保留官方语言选择，并在 Python 3.11 选择后重新设置虚拟环境及 `EXECD_ENVS`，确保远端 `python3` 使用同一套依赖。构建执行实际 import 和 `pip check`。示例构建入口是 `docker build --platform linux/amd64 -f deploy/opensandbox/Dockerfile -t causalagent-data-preparation:local deploy/opensandbox`。正式使用前将产物发布到实际镜像仓库，并将发布后的完整 digest 写入 `CAUSALAGENT_SANDBOX_IMAGE`。模板不提供该值，控制服务在启动监听前拒绝缺失值，Compose 仍可作静态解析；不能把官方基础镜像 digest 或本地 config ID 当作处理镜像 manifest digest。

默认网络配置面向 Linux Docker Engine，要求默认 bridge 网关为 `172.17.0.1`。部署前用 `docker network inspect bridge` 确认实际网关；不同则复制本目录 TOML、修改 `docker.publish_host` 并设置自定义配置路径。OpenSandbox 只加入 `sandbox_upstream`，controller 同时加入该网络和应用网络，Worker 不加入上游网络。两个 API 均不发布宿主端口。默认 bridge 上的临时沙盒端口只绑定宿主 bridge 网关，避免暴露到公网接口。

官方 Compose 示例要求 `[proxy] resolve_internal=false`、`docker.host_ip=host.docker.internal` 及 `host-gateway` 映射，才能让位于 Compose 网络的服务访问默认 bridge 沙盒的映射端口。controller 使用 SDK 的 `use_server_proxy=True`，执行及文件传输都经 OpenSandbox API；Worker 无需解析沙盒地址。`dns+nft`、禁用 IPv6、空宿主挂载许可列表与关闭自动续期是默认策略。没有配置企业出站代理；本轮沙盒默认禁止出站。

Docker 镜像拉取使用宿主 daemon 的网络及代理，与沙盒 egress 策略不同。不要把 `HTTP_PROXY` 或 `HTTPS_PROXY` 透传给 Worker 或临时沙盒以解决镜像下载问题。宿主仍需设置经过检查的 IPv4/IPv6 转发规则；真实验收必须覆盖控制 API、脚本执行、文件上传/回读、销毁及 IPv4/IPv6 禁止出站，不能只调用 health 接口。

Docker Desktop Linux amd64 的真实验证使用独立测试项目、临时密钥和原生 Docker secret 卷，不启动根业务 Compose。该平台不能使用示例的 `publish_host="172.17.0.1"`；测试配置改为 `publish_host="127.0.0.1"`，保留 `host.docker.internal` 与 `host-gateway`，实际 server proxy 可以访问沙盒。部署到该平台时，复制本目录 TOML 并通过 `CAUSALAGENT_OPENSANDBOX_CONFIG_SOURCE` 选择此配置。Windows 文件绑定不能证明 Linux 0640 权限；需要验证 Linux 权限时，应将临时统一密钥文件写入原生卷并设置 root:3000、0640，再只读挂载，由 `config/secret_file.py` 执行权限检查。该测试没有证明 Linux 裸 Engine 的默认配置或其他自定义网关。

2026-10-25 已实际拉取四个锁定的官方镜像。通用 Python 基础设施探测已通过有密钥 200、无密钥 401、SDK `use_server_proxy=True`、文件上传和回读 hash、UID 10001 执行、IPv4/IPv6 连接失败，以及销毁后 404。处理镜像也已实际构建并通过依赖 hash、import 和 `pip check`，本机 manifest 为 `causalagent-sandbox-probe-data-preparation@sha256:1f7362bba4a0a7c143b0964268a3836f6e5a86fdf1e1d90e3c0ac577a21aaeb8`；未向外部仓库发布。实际处理镜像已通过 `OpenSandboxRemote.create`、完整 `OpenSandboxSession._initialize`、固定脚本上传、非 root CSV 处理、预览与产物回读，以及销毁后 404。运行证据确认 UID 10001、`/opt/causalagent/venv/bin/python3`、numpy 1.26.4、pandas 3.0.5、scipy 1.17.1、scikit-learn 1.9.1；输入只读检查和四项 IPv4/IPv6 连接检查均通过。服务和隔离产物继续保留，完整控制服务、MySQL、Worker、模型和生产链路不能由这些结果替代。

本轮权限隔离范围为沙盒链路，仅 OpenSandbox 获得 Docker 权限。用户已确认保留开发与预发 Alloy 原有的 Docker socket 日志采集权限；`:ro` 不能限制 Docker API 操作，本次不改写该可观测性链路。
