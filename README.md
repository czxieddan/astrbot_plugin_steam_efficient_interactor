<div align="center">
<!-- Title: -->
<a href="https://github.com/czxieddan/">
 <img src="logo.png" height="200">
</a>
<h1>Steam高效互动者 - <a href="https://github.com/czxieddan/">Yezi & CzXieDdan</a></h1>
<p><strong>面向 AstrBot 的 Steam 状态监控插件</strong><br>
通过llm互动控制bot监控Steam指定玩家的状态变更与通知推送或查看信息。</p>
</div>
<div align="center">
<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-GPLv3-ff3a68?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/github/stars/czxieddan/astrbot_plugin_steam_efficient_interactor?style=for-the-badge&color=ffd700" alt="Stars">
  <img src="https://img.shields.io/badge/Platform-AstrBot-lightgrey?style=for-the-badge" alt="Platform">
</p>
</div>

---

<div align="center"><ul>

<p>稳定监控多个 SteamID</p>

<p>群维度管理玩家绑定关系</p>

<p>以图片形式优先展示状态变化</p>

<p>持久化保存关键配置和监控状态</p>

<p>为 AstrBot 提供可自然语言调用的查询工具</p>

</ul></div>

---

<div align="center">
  <h3>特别鸣谢</h3>
  <a href="https://github.com/czxieddan/astrbot_plugin_steam_efficient_interactor/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=czxieddan/astrbot_plugin_steam_efficient_interactor" />
  </a>
</div>

---

## 功能特性

- 支持群维度绑定多个 SteamID

- 支持监控玩家在线、离线、开始游戏、结束游戏

- 支持成就变化检测与图片推送

- 支持状态查询、群状态查询、最近事件查询

- 支持 Steam API Key 多 Key 轮换

- 支持运行时配置持久化，减少升级覆盖带来的配置丢失

- 支持图片优先输出，已有图片预设的场景不重复发送文字

- 支持 AstrBot LLM 工具调用与自然语言查询

## 安装方式

将插件目录放入 AstrBot 插件目录后，在 AstrBot 后台启用插件。

依赖如下：

- Python 3.10+
- httpx
- Pillow
- AstrBot

如果环境缺少依赖，可以执行：

```bash
pip install httpx pillow
```

## 配置项说明

插件配置由 AstrBot 插件配置页和运行时持久配置共同组成。  
关键配置会被镜像保存到数据目录，避免升级后丢失。

### 1. `steam_api_key`

- 类型：列表
- 说明：Steam Web API Key，支持多个 Key 轮换
- 配置方式：
  - 在 AstrBot 后台配置页中逐个添加
  - 也可传入逗号分隔字符串后被规范化
- 获取地址：
  - https://steamcommunity.com/dev/apikey
- 示例：

```text
["KEY_A", "KEY_B", "KEY_C"]
```

### 2. `sgdb_api_key`

- 类型：字符串
- 说明：SteamGridDB API Key，用于获取游戏封面图
- 获取地址：
  - https://www.steamgriddb.com/profile/preferences/api

### 3. `fixed_poll_interval`

- 类型：整数
- 说明：固定轮询间隔（秒）
- 默认值：`0`
- 含义：
  - `0`：启用智能轮询
  - `>0`：所有玩家统一固定间隔轮询

### 4. `retry_times`

- 类型：整数
- 说明：Steam API 请求失败后的重试次数
- 默认值：`3`

### 5. `detailed_poll_log`

- 类型：布尔
- 说明：是否输出详细轮询日志
- 默认值：`false`

### 6. `max_achievement_notifications`

- 类型：整数
- 说明：单次成就推送最多展示数量
- 默认值：`5`

### 7. `steam_group_mapping`

- 类型：列表
- 说明：预设 SteamID 与群号映射
- 支持格式：

```text
SteamID|群号
SteamID|平台:消息类型:会话ID
```

- 示例：

```text
76561198888888888|123456789
76561198888888888|aiocqhttp:GroupMessage:123456789
```

### 8. `enable_failure_blacklist`

- 类型：布尔
- 说明：失败后是否自动加入成就黑名单
- 默认值：`false`

## 配置优先级

插件运行时配置优先级如下：

```text
默认配置 < runtime_config.json < AstrBot 当前非空配置
```

这意味着：

- 当前 AstrBot 配置页中填写的非空 Steam API 配置会优先生效
- 运行时也会将关键配置镜像到数据目录
- 升级插件后，关键配置不应轻易丢失

## 指令大全

### 基础监控

- `/steam on`
  - 开启当前群 Steam 状态监控
- `/steam off`
  - 关闭当前群 Steam 状态监控
- `/steam list`
  - 查看当前群玩家状态列表图
- `/steam alllist`
  - 查看所有群和玩家状态文本列表
- `/steam rs`
  - 重置监控缓存与运行状态

### SteamID 管理

- `/steam addid [SteamID]`
  - 添加一个或多个 SteamID 到当前群
  - 支持逗号或空格分隔多个 ID
- `/steam delid [SteamID]`
  - 从当前群删除 SteamID
- `/steam clear_allids`
  - 清空所有群的 SteamID 与状态数据

### 配置与诊断

- `/steam config`
  - 查看当前配置
- `/steam set [参数] [值]`
  - 修改配置项
- `/steam api_status`
  - 查看当前生效 API Key 诊断状态
- `/steam清除缓存`
  - 清理头像、封面等图片缓存

### 成就推送

- `/steam achievement_on`
  - 开启当前群成就推送
- `/steam achievement_off`
  - 关闭当前群成就推送

### 查询与调试

- `/steam openbox [SteamID]`
  - 查看指定 SteamID 的较完整状态信息
- `/steam test_achievement_render [steamid] [gameid] [数量]`
  - 测试成就图片渲染
- `/steam test_game_start_render [steamid] [gameid]`
  - 测试开始游戏图片渲染
- `/steam test_game_end_render [steamid] [gameid] [duration_min] [end_time] [tip_text]`
  - 测试结束游戏图片渲染
- `/steam help`
  - 查看帮助

## 自然语言调用说明

插件提供了可供 AstrBot LLM 调用的查询工具，因此在启用函数调用能力时，可以直接自然语言提问。

### 可自然语言触发的常见查询

- 看看本群用户状态
- 查一下这个群的 Steam 玩家状态
- 查询最近 Steam 事件
- 查某个 SteamID 的完整状态
- 看看这个群绑定了哪些 Steam 号
- 开启这个群的 Steam 监控
- 关闭这个群的 Steam 成就推送

### 已提供的文本/图片工具方向

- 群用户状态查询
- 单玩家状态查询
- 最近事件查询
- 监控总览查询
- 群绑定摘要查询
- 群状态图片
- 单玩家状态图片

## 输出规则

- 已有图片预设的场景优先图片，不重复补发文字
- 图片生成失败时，才回退到文字说明
- 图片中不渲染无必要的固定小标题、固定吐槽文案、装饰性标题
- 文本工具遵守 AstrBot 原生工作链

## 常见问题排查

### 1. 查询结果全部是 401 / 403

这通常更像是 API Key 问题，而不是玩家隐私设置问题。

先检查：

- AstrBot 插件配置页里是否真的填写了有效 `steam_api_key`
- 是否配置了多个 Key 但都已失效
- `/steam api_status` 输出中当前生效的 key 数量是否正确
- 控制台中是否出现 API 轮换诊断日志

### 2. 配置看起来填了，但运行时像没生效

请优先检查：

- AstrBot 当前配置页
- `/steam api_status`
- 数据目录中的 `runtime_config.json`
- 控制台中的 `[steam_status_monitor] API配置诊断[...]` 日志

### 3. 图片和文字同时发送

当前设计为：

- 有图时只发图
- 无图时才发文字

如果仍出现双发，应重点检查：

- 是否是旧版本插件未完全重启
- 是否是其他插件或上游逻辑重复发送

### 4. 看不到谁在线或谁在玩什么

可能原因：

- Steam API Key 失效
- 网络环境异常
- Steam 接口临时拒绝
- 玩家资料权限关闭
- 绑定的 SteamID 本身无效

### 5. 修改配置后没有立刻生效

部分配置项在运行时会动态生效，但某些场景建议重启 AstrBot 后再验证，以避免旧后台任务残留。

## 轮询说明

当 `fixed_poll_interval = 0` 时，插件使用智能轮询：

- 正在游戏：约 60 秒
- 在线：约 60 秒
- 刚离线：约 60 秒
- 离线 3 小时内：约 300 秒
- 离线 24 小时内：约 600 秒
- 离线 48 小时内：约 1200 秒
- 更久：约 1800 秒

## 借鉴来源

本项目借鉴自[Steam 状态监控插件V2](https://github.com/Maoer233/astrbot_plugin_steam_status_monitor)


