# Project Agent Notes

## 基本规则

- 默认用中文回答。
- 本仓库是 Chrome MV3 扩展，根目录本身就是可加载的扩展目录；没有前端构建步骤。
- 涉及外部网络事务，优先使用国内源；GitHub / Microsoft / OpenAI 直连失败时使用本机代理 `127.0.0.1:10808`。
- 后台脚本不要占用前端窗口；长期运行的 helper 或服务应使用隐藏/独立后台进程。
- Windows + PowerShell 环境，命令示例要使用 PowerShell 支持的语法。
- 修改完成后按用户要求提交并推送到当前 fork：`origin=https://github.com/yufeinever/GuJumpgate.git`。

## 当前仓库状态

- 当前主线已同步上游 `FoundZiGu/GuJumpgate` 的 `v0.1.3`。
- `upstream` 远端用于同步原仓库，`origin` 远端用于推送用户自己的仓库。
- `manifest.json` 可能包含本地扩展 `key`，用于维持本机加载扩展时的固定扩展 ID；同步上游时不要无意删除。
- `downloads/` 只用于存放 release zip 和解压产物，不提交。

## 运行和依赖

- 扩展本体无需 `npm install`；`package.json` 只有项目元数据。
- 安装/测试扩展时，在 Chrome/Edge 扩展页加载仓库根目录或 release 解压目录，并开启无痕权限。
- 本地 JSON 导出和 Hotmail 本地收信依赖 helper：

```powershell
cd D:\GuJumpgate
.\start-hotmail-helper.bat
```

- `v0.1.3` 新增 `services/checkout-converter` 服务。其 Python 依赖位于 `services/checkout-converter/requirements.txt`：
  - `fastapi==0.115.12`
  - `uvicorn==0.34.2`
  - `gunicorn==23.0.0`
  - `curl_cffi>=0.14.0`

本地启动示例：

```powershell
cd D:\GuJumpgate\services\checkout-converter
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CHECKOUT_CONVERTER_API_KEY="replace-me"
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8080
```

## Hotmail / Outlook Token 方案

插件的 Hotmail API 对接不是网页登录收信，而是用 `client_id + refresh_token` 换取 Microsoft Graph/Outlook API access token。有效 token 需要授权：

```text
offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read
```

遇到 `AADSTS70000` 或 `one or more scopes requested are unauthorized or expired` 时，通常表示：

- refresh token 不是由当前 `client_id` 生成；
- 生成 refresh token 时没有授权 `Mail.Read/User.Read/offline_access`；
- 授权被撤销、过期、改密或触发风控；
- 当前 `client_id` 的 Azure 应用没有允许对应 delegated permissions。

本地辅助脚本：

```powershell
cd D:\GuJumpgate
python scripts\generate_ms_refresh_token.py --line "email@hotmail.com----password----client_id"
```

脚本使用 Microsoft OAuth authorization code + PKCE 流程，会打开浏览器让用户手动登录并同意授权，成功后输出插件导入格式：

```text
邮箱----密码----client_id----refresh_token
```

注意：

- 这一步必须用户手动在 Microsoft 页面登录并同意权限，不能只靠邮箱密码静默完成。
- 如果页面报 `redirect_uri is not valid`，说明该 `client_id` 未注册脚本使用的本地回调地址；需要换支持本地回调的 Microsoft app client_id，或在 Azure App Registration 中添加 `http://localhost:17374`。
- 设备码授权对部分 client_id 会报 `The client application must be marked as 'mobile.'`，因此当前脚本默认使用浏览器授权码回调。

## 同步上游

同步上游 release 时：

```powershell
cd D:\GuJumpgate
$env:HTTPS_PROXY='http://127.0.0.1:10808'
$env:HTTP_PROXY='http://127.0.0.1:10808'
git fetch upstream --tags
git merge --ff-only v0.1.3
```

同步前后都要检查：

```powershell
git status --short --branch
git remote -v
```
