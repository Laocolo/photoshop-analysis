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
- **可换模型**：设置里可改 base_url 和模型名，兼容任何 OpenAI 格式的 API。例如火山方舟（豆包）：Base URL 填 `https://ark.cn-beijing.volces.com/api/v3`，模型填推理接入点 ID（`ep-xxx`）或视觉模型名（如 `doubao-seed-1.6-vision`），Key 在方舟控制台"API Key 管理"创建——注意必须用带视觉能力的模型，纯文本模型无法点评图片

## 运行

**直接使用（推荐）**：双击 `dist/摄影点评助手.exe` 即可，无需安装 Python。

首次启动会提示配置 API Key，点"设置 API Key…"填入即可。Key 和历史点评会保存在 exe 同目录的 `config.json` 和 `history/`。

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
