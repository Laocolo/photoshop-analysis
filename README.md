# 摄影学习点评助手

一个 Windows 桌面小工具：导入照片后自动读取 EXIF 拍摄参数，调用 AI 视觉大模型（默认 Kimi）给出老师级点评——总评（好图/普通/较差）、亮点（构图框架、景别、用光）、不足、参数分析和改进建议，帮你学摄影。

## 功能

- **导入照片**：点按钮选择或直接拖入，自动解析光圈/快门/ISO/焦距/拍摄时间/相机型号；EXIF 缺失时（截图、微信保存的图）可手动填写
- **支持 RAW**：可直接导入 `.arw/.cr2/.nef/.dng/.rw2/.orf/.raf`（抽取内嵌预览图用于显示和点评，参数直读 RAW；佳能新格式 `.cr3` 暂不支持）。RAW+JPG 双拍时直接导 JPG 也一样，两者 EXIF 完全相同
- **支持 HEIC**：iPhone 默认的"高效"格式（`.heic`）可直接导入，参数正常读取。注意：微信传输会剥掉 EXIF，发送时需勾选"原图"
- **旋转调整**：载入时按 EXIF 方向标记自动转正；预览下方的"↺ 向左转 / ↻ 向右转"可再手动旋转 90°，点评和历史存档都会使用旋转后的画面
- **补充信息**：可填"大概时间/光线"和"我想拍什么"，让点评更贴合你的意图
- **AI 点评**：按固定结构输出——总评（评级+10 分制+一句话）→ 亮点（三分法/引导线/框架式/对称/留白等构图框架、远全中近特景别、用光色彩）→ 不足 → 参数分析 → 后期调整建议（按 Lightroom / Camera Raw 调整项给出裁剪、曝光、白平衡、HSL、锐化等具体步骤）→ 改进建议
- **历史记录**：每次点评自动保存（缩略图+参数+结果），右下列表点击回看
- **多模型与服务商管理**：兼容任何 OpenAI 格式的 API。设置里可把多套配置（base_url + 模型 + Key）**存为"服务商"**，之后下拉一键切换，不用每次重填；内置 Kimi（Moonshot）、Kimi Code、豆包（火山方舟）、Agnes AI 模板，选模板后只需粘贴 Key。注意必须用带视觉能力的模型，纯文本模型无法点评图片。各厂商要点：
  - Kimi（Moonshot）：`https://api.moonshot.cn/v1` + `kimi-latest`，Key 在 <https://platform.moonshot.cn> 创建
  - Kimi Code：`https://api.kimi.com/coding/v1` + `kimi-for-coding`
  - 豆包（火山方舟）：`https://ark.cn-beijing.volces.com/api/v3`，模型填推理接入点 ID（`ep-xxx`）或视觉模型名（如 `doubao-seed-1.6-vision`），Key 在方舟控制台"API Key 管理"创建
  - Agnes AI：`https://apihub.agnes-ai.com/v1` + `agnes-2.0-flash`，Key 在 <https://platform.agnes-ai.com> 创建（其视觉能力对 base64 图片的支持以实测为准，不行可换 `agnes-2.5-flash` 试试）

## 运行

**直接使用（推荐）**：双击 `dist/摄影点评助手.exe` 即可，无需安装 Python。

首次启动会提示配置 API Key，点"设置 API Key…"填入即可。Key 和历史点评会保存在 exe 同目录的 `config.json` 和 `history/`。

## 手机使用（网页 / PWA）

不用装 App：在电脑上起一个网页服务，iPhone 浏览器直接使用，还能"添加到主屏幕"变成全屏应用。

1. 启动服务：

   ```bash
   .venv/Scripts/python server.py
   ```

   首次启动若弹出 Windows 防火墙提示，勾选"专用网络"并允许。启动后窗口会打印局域网地址（如 `http://192.168.10.111:8000`）。
2. 手机与电脑连**同一 Wi-Fi**，用 Safari 打开该地址。
3. （推荐）Safari 底部分享按钮 →「添加到主屏幕」，之后从主屏幕图标启动即为全屏体验。
4. 首次使用点右上角 ⚙️ 填入 API Key（Base URL / 模型与桌面版相同，如 Kimi Code 填 `https://api.kimi.com/coding/v1` + `kimi-for-coding`）。

使用流程与桌面版一致：选照片（iPhone 相册的 HEIC 会由 Safari 自动转 JPG 上传，EXIF 参数保留）→ 自动读取参数 → 方向不对可点"旋转 90°"→ 开始点评。点评历史保存在电脑的 `history/`。

**访问密码**：网页版配置保存在项目根的 `config.json`（与 exe 的 `dist/config.json` 相互独立）。仅家庭局域网使用可不设密码；若要通过 cpolar / 花生壳等隧道公网访问，务必先在设置里填"访问密码"，之后所有请求都需输入该密码。

**从源码运行**（开发用）：

## 获取 Kimi API Key

1. 注册 <https://platform.moonshot.cn>
2. 充值（新用户有赠送额度，点评一张照片约几分钱）
3. 用户中心 → API Key 管理 → 新建，把 `sk-...` 填进软件设置
4. 点"测试连接"确认可用

Key 保存在项目目录的 `config.json`，历史点评在 `history/`（两者都不会上传，已在 .gitignore 排除）。

## 打包成 exe（可选）

```bash
.venv/Scripts/pip install pyinstaller
.venv/Scripts/pyinstaller --noconsole --onefile --name 摄影点评助手 --hidden-import=rawpy --hidden-import=exifread main.py
```

产物在 `dist/摄影点评助手.exe`。

## 依赖

Python 3.11+，依赖见 `requirements.txt`（PySide6 / Pillow / requests）。
