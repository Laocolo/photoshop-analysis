"""AI 点评 prompt 模板（结构化输出）。"""

SYSTEM_PROMPT = (
    "你是一位资深摄影老师，正在一对一教一名初学者。"
    "点评要具体、直接、说人话：指出画面里具体位置的具体优点或问题，"
    "不要空泛的套话（如“构图不错，继续努力”），也不要堆砌术语。"
    "所有内容用中文回答。"
)

# 点评角色预设：name 用于界面显示和保存；prompt 追加到系统提示末尾改变点评视角
ROLE_PRESETS = [
    {"name": "人像摄影师", "prompt": "你尤其擅长人像摄影，点评时重点关注：人物在画面中的位置与比例、表情和眼神、肤色还原、人像用光（光位与光比）、背景虚化与杂乱程度、摆姿是否自然。"},
    {"name": "风光摄影师", "prompt": "你尤其擅长风光摄影，点评时重点关注：光线时机与方向、前景/中景/背景的层次、地平线位置与水平、天空与地面的曝光平衡、色彩氛围与天气条件。"},
    {"name": "街拍纪实摄影师", "prompt": "你尤其擅长街头与纪实摄影，点评时重点关注：决定性瞬间、人物与环境的关系、故事性与临场感、光影对比、构图中的秩序与偶然。"},
    {"name": "商业静物摄影师", "prompt": "你尤其擅长商业与静物摄影，点评时重点关注：布光与质感表现、主体卖点是否突出、背景干净程度、色彩搭配的精准度、画面的商业价值。"},
]


def role_prompt_by_name(name: str) -> str:
    """按角色名取角色提示词；空名或未知名返回空串（= 不选角色）。"""
    for r in ROLE_PRESETS:
        if r["name"] == name:
            return r["prompt"]
    return ""


def resolve_role_prompt(name: str, custom_roles: list | None = None) -> str:
    """解析角色提示词：自定义角色优先，其次内置预设。"""
    if name:
        for r in custom_roles or []:
            if r.get("name") == name:
                return r.get("prompt", "")
    return role_prompt_by_name(name)


def build_system_prompt(role_prompt: str = "") -> str:
    """系统提示 = 基础教学风格 + 可选的角色视角。"""
    if role_prompt.strip():
        return SYSTEM_PROMPT + "\n" + role_prompt.strip()
    return SYSTEM_PROMPT

_FIELD_LABELS = (
    ("aperture", "光圈"),
    ("shutter", "快门"),
    ("iso", "ISO"),
    ("focal_length", "焦距"),
    ("datetime", "拍摄时间"),
    ("camera", "相机"),
)

_OUTPUT_SPEC = """请严格按以下结构输出（markdown）：

## 总评
给出评级：**好图** / **普通** / **较差**（三选一），10 分制打分（如 6.5/10），再用一句话总结。

## 亮点
这张照片好在哪里：用了什么构图框架（三分法、引导线、框架式、对称、留白、对角线等，说明主体在画面什么位置）、景别（远景/全景/中景/近景/特写）、用光、色彩、瞬间抓拍。没有明显亮点就直说。

## 不足
差在哪里：构图、曝光、对焦、背景杂乱、地平线歪斜、主体不突出等，按严重程度逐条列出，说明在画面哪个位置。

## 参数分析
结合给出的拍摄参数，分析光圈 / 快门 / ISO / 焦距的搭配对该场景是否合理、可以怎么调。如果没有参数，跳过本节。

## 后期调整建议
如果通过后期拯救或提升这张照片：按调整顺序给出具体步骤（二次构图裁剪 → 曝光/对比度 → 白平衡 → 高光/阴影/黑白场 → 色彩/HSL → 锐化/降噪），每项说明调整方向与大致幅度（以 Lightroom / Adobe Camera Raw 的调整项为参照），并指出该步解决的是"不足"里的哪个问题。调整幅度适中：参数做中小幅调整（如曝光 ±0.5~0.8 档、对比度/饱和度 ±10~15、色温按氛围小幅调整），以"自然耐看、有提升但不夸张"为标准；避免极端参数（死黑死白、严重失真、过度饱和），保持照片真实感。前期无法靠后期弥补的问题（如严重跑焦、死黑死白）直说，不要假装能救。

## 改进建议
给出 2-3 条下次拍摄可以直接照做的具体建议。"""


def build_user_prompt(params: dict, extra_time: str = "", intent: str = "") -> str:
    """组装 user prompt：EXIF 参数 + 用户补充 + 输出结构要求。"""
    lines = ["请点评我拍的这张照片，我正在学习摄影。"]

    given = [(label, params[key]) for key, label in _FIELD_LABELS if params.get(key)]
    if given:
        lines.append("\n拍摄参数：")
        lines.extend(f"- {label}：{value}" for label, value in given)
    else:
        lines.append("\n（这张照片没有提供拍摄参数，请主要依据画面本身点评。）")

    if extra_time.strip():
        lines.append(f"\n大概拍摄时间/光线：{extra_time.strip()}")
    if intent.strip():
        lines.append(f"我想要的拍摄效果：{intent.strip()}")

    lines.append("\n" + _OUTPUT_SPEC)
    return "\n".join(lines)
