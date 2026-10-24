# Misaka + pakku：最新发送弹幕与重复合并

此集成基于 Misaka v2.8.9；浏览器 pakku 引擎固定引用 dahaox3/pakku.js 的 3477ee088217c6a1bd258c7c00d73097cef38ec9。

- 输出设置中提供全部、最新1000/3000/5000/10000条与自定义数量。
- 直接复用用户 fork 的 `select_latest`：按发送日期排序，超大ID以十进制字符串精确比较；先选择，再合并。
- 最新模式目前支持B站。按库中准确的 aid,cid 获取当前XML与分段池，按弹幕ID去重，保留发送日期；不会按同一集号取第一条预告。
- 只改变播放器请求的输入候选，不改写原XML/数据库弹幕。原鉴权、配额、黑名单、简繁、位置/颜色和数量限制继续由原应用执行。
- 请求作用域的读取钩子不会改变后台任务和普通库读取；最新结果不读写原历史采样缓存。源站结果缓存最多5分钟，体积上限16MiB，有并发合并、超时、失败退避与大小限制。
- 来源失败或无日期明确报错，不回填历史弹幕。当前公开接口并不保证覆盖平台全量历史；最新N条也不等于最近N天。
- 最新筛选与重复合并开关独立。默认latestCount=0（保留原全部输出）；其他27项既有合并参数保持。
- 原有输出网关及JSON/XML兼容、字号/密度优选继续保留。Yamby逐条字号渲染仍取决于播放器支持，不承诺已验证。

## 构建

```sh
git submodule update --init --recursive
cd integrations/pakku
npm ci
npm run build
npm test
cd ../../web
npm ci
npm run build
```

引擎 `engine.mjs`、WASM、Node可执行文件与前端dist都是构建/发布产物，不应提交运行数据或真实凭据。使用 Node 24.22.1 或更新的已修复24.x运行时，核对 nodejs.org 官方校验清单。

部署通过 `package_release.py` 生成覆盖包，挂载独立的pakku入口、网关、新增src模块和前端。不要覆盖/修改官方受保护的comments.py、default_configs.py或已编译弹幕源，也不要取消完整性校验。`run-gateway.sh`保留原exec.sh降权和生命周期。具体Compose示例见compose.example.yaml；沿用原数据库/持久目录和服务认证配置。

回滚：停用最新筛选（latestCount=0）或恢复上一份Compose挂载并只重建Misaka/Pakku；不恢复整库，不删除原弹幕文件。第一次上线先备份当前Compose和数据库，验证未登录401、原库文件哈希及真实播放器接口。

## 测试

`test-engine.mjs`保留19项合并/显示行为测试；`test-recent.mjs`覆盖发送日期排序、大整数ID、未知日期和先筛选后计数。Python `test_recent.py`检查协议、合并池、缓存与任务隔离，`test_gateway.py`覆盖鉴权/待处理/错误透传及JSON/XML输出。Python测试在安装原Misaka运行依赖的环境中运行，先导入src.main加载路由，但不执行应用lifespan或调度器。

## 许可

Misaka及Python/前端集成为AGPL-3.0（见项目LICENSE）。Node侧车、adapter和density代码为GPL-3.0-or-later，使用GPLv3的pakku核心与用户fork的recent.ts，许可证随包提供；两者通过私有网络JSON通信。对应源代码和构建脚本随`pakku-integration-source.tar.gz`提供，下载包不含数据库、API令牌、账号、SSH私钥或代理订阅。
