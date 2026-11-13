---
name: data-dev-skill-sync
description: data-dev-skills 市场的客户端元技能。当用户说"更新 data-dev 技能、同步技能、技能同步、检查技能新版本、安装 data-dev skills、skill 更新"，或贴出本仓库 docs/install.md 链接要求安装时使用。执行订阅脚本完成 git 拉取并把 marketplace.json 登记的技能分发到本机各 harness 技能目录（Claude Code / Codex / Cursor / WorkBuddy / ZCode / 通用 .agents 等），按技能引导补齐 .env，并汇报版本变化。
---

# data-dev-skill-sync · data-dev-skills 市场客户端

> 你负责帮用户把 `data-dev-skills` 市场里的技能更新到本机。所有实际动作用
> `scripts/subscribe.py` 完成，你不要手工复制文件——脚本会做排除（.env/output）、
> 先删后拷的完整镜像和版本记录，手工复制会破坏这些保证。

## 定位关键路径

1. 优先读订阅状态文件 `~/.data-dev-skills/subscription.json`（Windows 为
   `%USERPROFILE%\.data-dev-skills\subscription.json`），其中的 `repo` 字段是市场仓库
   的本地 clone 路径，`harnesses` 是已订阅的分发目标。
2. 若状态文件不存在：检查默认 clone 位置 `~/.data-dev-skills/repo` 是否存在；不存在就按
   下方「配置 Git 凭据」clone（公开仓库可匿名 clone；私有 fork 需要用户提供账号 + 令牌），
   然后进入"首次订阅"流程。

## 配置 Git 凭据（仅私有仓库需要）

公开仓库直接 `git clone https://github.com/<your-org>/data-dev-skills.git` 即可。若用户用的是
私有 fork：

1. 让用户生成 GitHub **Personal access token**（Fine-grained token 勾选目标仓库的
   `Contents: Read` 即可；classic token 勾选 `repo`）。
2. 让用户把 **GitHub 用户名 + 令牌** 发给你。**不要把令牌回显到对话/文件里**，也不写进文档或提交。
3. 带凭据 clone 到 `~/.data-dev-skills/repo`（Windows：`%USERPROFILE%\.data-dev-skills\repo`）：

   ```bash
   git clone "https://<user>:<token>@github.com/<your-org>/data-dev-skills.git" <目录>
   ```

   令牌含 `@`、`:` 等特殊字符时先做 URL 编码。
4. clone 目录已存在但 `git pull` 报 401/403 → 让用户重新生成令牌，再
   `git remote set-url origin "https://<user>:<new-token>@github.com/<your-org>/data-dev-skills.git"` 重试。

## 更新已订阅技能（最常见）

执行（把 `<repo>` 替换为状态文件里的 repo 路径）：

```bash
python "<repo>/scripts/subscribe.py" --sync
```

把输出翻译给用户：每个技能是"新增安装 / 升级 旧版本 -> 新版本 / 已是最新"，
以及本次拉取到的提交列表（取前几条，用一句话概括改动主题）。

**输出末尾若出现 `⚠️ 以下技能需要配置环境变量`，必须逐技能引导用户补齐 `.env`**
——漏掉这步技能无法运行。步骤见下方「引导环境变量配置」。已配置的用户不会看到该提醒，
无需重复提醒。

## 引导环境变量配置（缺配时）

1. 先看某技能要哪些变量、当前缺哪些（**不显示值**）：

   ```bash
   python "<repo>/scripts/setup_env.py" --skill <技能名> --list
   ```

2. 按技能口径向用户要值，**不要在对话里回显凭据**：

   - **master-data-modeling**：`MD_OPERATOR` 让用户填**自己的账号/花名**；其余
     `MYSQL_*` / `PANEL_BASE_URL` / `KS_BASE_URL` / `TDAI_USER_KEY` **找数据平台管理员提供**；
     `DDM_*` **可不提供**（只在"同步术语根"时才需要，不配置不影响建模主流程）。
   - **master-data-source-mapping / model-lineage-impact**：`DATADEV_API_TOKEN` 让用户到
     所用元数据/调度服务的控制台生成**个人访问令牌**；权限不够找平台管理员。若用户的服务
     不是默认的 JSON-RPC MCP 形状，让用户按 `docs/adapter.md` 复制
     `adapter-configs/example.mcp.json`（或 `example.rest.json`）改成本地配置，并用
     `DATADEV_ADAPTER_CONFIG` 指向该文件。

3. 拿到值后用脚本写入（值不回显，可一次写多个）：

   ```bash
   python "<repo>/scripts/setup_env.py" --skill <技能名> --set KEY=VALUE [--set KEY=VALUE ...]
   ```

4. 复核：再跑一次 `--list`，确认没有"缺失 / 待填(占位符)"。

变量口径的权威来源是仓库根 `marketplace.json` 的 `skills[].env`（是否必填 / 谁提供 /
获取引导）与 `docs/install.md`「环境变量配置」，两者不一致时以文档为准。

## 首次订阅（状态文件不存在时）

确认用户想把技能装进哪些 harness（可多选：`agents` / `claude` / `codex` / `cursor` /
`workbuddy` / `zcode`；不确定就推荐 `agents` + 用户在用的那一两个），然后：

```bash
python "<repo>/scripts/subscribe.py" --harness agents --harness claude
```

装完后提醒用户：**新对话**里 agent 才会发现新技能；WorkBuddy 用户还需在
"技能"页确认技能已出现（详见仓库 `docs/workbuddy.md`）。接着按上面的
「引导环境变量配置」把缺配技能一次性配好。

## 用户要求自动更新时

注册每日 09:30 的计划任务（Windows 计划任务 / macOS·Linux crontab）：

```bash
python "<repo>/scripts/subscribe.py" --register-task
```

移除用 `--unregister-task`；查看订阅用 `--status`；卸载技能用 `--uninstall`。

## 边界

- `git pull` 失败（离线/本地改动）时脚本会用本地版本继续并在 stderr 给出警告，
  如实转述即可，不要尝试替用户修 git 状态。
- 不要修改用户 `~/.claude/skills`、`~/.agents/skills` 等目录下的任何文件；
  分发/清理一律走脚本。
- 涉及在 WorkBuddy 手工上传 ZIP 的场景（无法访问 git 的机器），引导用户使用
  Release 打包产物，见 `docs/workbuddy.md`。
